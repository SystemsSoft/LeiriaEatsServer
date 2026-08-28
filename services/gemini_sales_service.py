"""
Gemini Sales Service
Serviço de IA generativa usando Google Gemini 1.5 Flash para conversação de vendas
com cache inteligente e monitoramento de uso (Otimizado para PT-PT)
"""
from google import genai
from google.genai import types
from typing import Dict, List, Optional
import time
from datetime import datetime
from core.config import settings


class GeminiCache:
    """Sistema de cache simples para economizar requisições"""

    def __init__(self, ttl_seconds=3600):
        self._cache = {}
        self._ttl = ttl_seconds

    def get(self, key: str) -> Optional[str]:
        """Recupera do cache se ainda válido"""
        if key in self._cache:
            value, timestamp = self._cache[key]
            if time.time() - timestamp < self._ttl:
                return value
            else:
                # Expirado
                del self._cache[key]
        return None

    def set(self, key: str, value: str):
        """Salva no cache com timestamp"""
        self._cache[key] = (value, time.time())

    def clear(self):
        """Limpa todo o cache"""
        self._cache.clear()

    def size(self) -> int:
        """Retorna tamanho do cache"""
        return len(self._cache)


class GeminiUsageMonitor:
    """Monitora uso da API Gemini (limite: 1500 req/dia)"""

    def __init__(self):
        self._requests_today = 0
        self._last_reset = datetime.now().date()
        self._total_requests = 0

    def record_request(self):
        """Registra uma requisição"""
        # Reset diário
        today = datetime.now().date()
        if today > self._last_reset:
            self._requests_today = 0
            self._last_reset = today

        self._requests_today += 1
        self._total_requests += 1

    def can_make_request(self) -> bool:
        """Verifica se pode fazer requisição (limite 1500/dia)"""
        return self._requests_today < 1500

    def get_status(self) -> Dict:
        """Retorna status de uso"""
        return {
            "requests_today": self._requests_today,
            "limit_daily": 1500,
            "remaining_today": max(0, 1500 - self._requests_today),
            "percentage_used": (self._requests_today / 1500) * 100,
            "total_lifetime": self._total_requests
        }


class GeminiSalesAgent:
    """
    Agente de vendas usando Google Gemini 1.5 Flash
    Modelo otimizado para conversação consultiva de vendas em Portugal
    """
    _clients = []
    _is_initialized = False
    _cache = GeminiCache(ttl_seconds=1800)  # Cache de 30 minutos
    _usage_monitor = GeminiUsageMonitor()
    _ultimo_tamanho_prompt = 0  # F0: exposto para a telemetria estimar tokens de entrada

    # F2.3: roteamento de modelo por turno.
    # gemini-1.5-flash e gemini-2.5-flash(-lite) foram descontinuados por esta conta
    # (404 NOT_FOUND) — confirmado ao vivo em 2026-08-27. "gemini-flash-lite-latest" é
    # o alias que o próprio Google mantém apontado para a versão estável atual do tier
    # Lite, evitando cair de novo nesse mesmo problema quando a próxima geração sair.
    # "gemini-flash-latest" (tier mais forte) foi testado no mesmo dia e falhou nas 4
    # chaves configuradas — 429 RESOURCE_EXHAUSTED (cota do tier gratuito, 20 req/dia
    # para o modelo por trás do alias) numa tentativa e 503 UNAVAILABLE (sobrecarga do
    # Google) em outra. Os três apontam para o mesmo modelo por ora — reavaliar usar um
    # tier mais forte em MODELO_ACAO quando o faturamento pay-as-you-go for ativado.
    MODELO_CONVERSA = "gemini-flash-lite-latest"
    MODELO_ACAO = "gemini-flash-lite-latest"
    MODELO_ESTAVEL = "gemini-flash-lite-latest"

    @classmethod
    def _escolher_modelo(cls, context: Dict) -> str:
        """Turno com carrinho não-vazio ou intenção de busca/pergunta específica de
        produto merece o modelo com melhor aderência a instrução."""
        if context.get("cart"):
            return cls.MODELO_ACAO
        if context.get("intent_type") in {"product_search", "specific_question"}:
            return cls.MODELO_ACAO
        return cls.MODELO_CONVERSA

    # F3.1 (PLANO_EXECUCAO_IA.md): ferramentas do contrato de function calling, que
    # substitui as tags textuais [[ADD_TO_CART:...]]/[[SHOW_CART]]. Formato validado por
    # introspecção do SDK google-genai 2.17.0 instalado neste projeto (não assumido de
    # memória) — types.FunctionDeclaration aceita JSON Schema puro via
    # `parametersJsonSchema`. O round-trip completo (chamada real → function_call →
    # function_response → texto final) NÃO pôde ser validado contra a API ao vivo nesta
    # sessão porque a GEMINI_API_KEY do projeto está com créditos esgotados (429
    # RESOURCE_EXHAUSTED, confirmado com uma chamada mínima). Validar assim que os
    # créditos forem restabelecidos, antes de ligar IA_FUNCTION_CALLING em produção.
    _FERRAMENTA_ADICIONAR_AO_CARRINHO = types.FunctionDeclaration(
        name="adicionar_ao_carrinho",
        description=(
            "Adiciona, remove ou ajusta a quantidade de um produto no carrinho do cliente. "
            "delta_quantidade positivo adiciona/incrementa; negativo remove/decrementa. "
            "Use o GID exato listado em PRODUTOS DISPONÍVEIS — nunca invente um GID."
        ),
        parametersJsonSchema={
            "type": "object",
            "properties": {
                "product_gid": {"type": "string", "description": "GID exato do produto listado no catálogo"},
                "delta_quantidade": {"type": "integer", "description": "Variação de quantidade; negativo remove"},
            },
            "required": ["product_gid", "delta_quantidade"],
        },
    )
    _FERRAMENTA_MOSTRAR_SACOLA = types.FunctionDeclaration(
        name="mostrar_sacola",
        description="Sinaliza que a sacola/carrinho deve ser exibida ao cliente para revisão e pagamento, após ele confirmar que quer finalizar o pedido.",
        parametersJsonSchema={"type": "object", "properties": {}},
    )
    # F4.2: substitui _filter_mentioned_products (heurística de casar nome de produto no
    # texto da resposta, que erra em ambas as direções — ver ANALISE_IA_PEDIDO.md 3.6).
    # O modelo declara explicitamente quais produtos do pool merecem aparecer como
    # cartão ao cliente, em vez do servidor adivinhar a partir do texto gerado.
    _FERRAMENTA_SUGERIR_PRODUTOS = types.FunctionDeclaration(
        name="sugerir_produtos",
        description=(
            "Declara quais produtos do catálogo devem ser exibidos como cartão ao cliente nesta "
            "resposta (ex.: ao comparar opções ou recomendar algo), mesmo sem adicioná-los ao "
            "carrinho. Não é necessário chamar isto para produtos que você já adicionou via "
            "adicionar_ao_carrinho — esses já aparecem automaticamente."
        ),
        parametersJsonSchema={
            "type": "object",
            "properties": {
                "gids": {"type": "array", "items": {"type": "string"},
                          "description": "GIDs exatos do catálogo a destacar, na ordem de relevância"},
            },
            "required": ["gids"],
        },
    )
    _TOOLS = [types.Tool(functionDeclarations=[
        _FERRAMENTA_ADICIONAR_AO_CARRINHO, _FERRAMENTA_MOSTRAR_SACOLA, _FERRAMENTA_SUGERIR_PRODUTOS,
    ])]

    # Respostas em cache adaptadas para o vocabulário e tom de PT-PT
    _STATIC_RESPONSES = {
        "oi": "Olá! 😊 O que gostaria de encomendar hoje?",
        "olá": "Olá! Como posso ajudar? 😊",
        "bom dia": "Bom dia! 😊 Pronto para fazer o seu pedido?",
        "boa tarde": "Boa tarde! 😊 O que vai desejar hoje?",
        "boa noite": "Boa noite! 😊 Vamos tratar do seu pedido?",
        "obrigado": "Ora essa! 😊 Precisa de mais alguma coisa?",
        "obrigada": "Ora essa! 😊 Posso ajudar em mais algo?",
        "tchau": "Até logo! 😊 Volte sempre!",
    }

    @classmethod
    def initialize(cls):
        """Inicializa os clientes Gemini (com Failover)"""
        if cls._is_initialized:
            return

        try:
            print(f"🤖 [Gemini] Configurando API ({len(settings.GEMINI_API_KEYS)} chaves detectadas)...")

            cls._clients = []
            for key in settings.GEMINI_API_KEYS:
                client = genai.Client(api_key=key)
                cls._clients.append(client)
            
            if not cls._clients:
                raise ValueError("Nenhuma GEMINI_API_KEY configurada no .env")

            # 🚀 Pré-configurar regras do sistema (System Instructions)
            # Isso torna a geração mais rápida e o prompt mais curto
            cls._system_instruction = """Você é um CONSULTOR DE VENDAS especialista em delivery de comida em Portugal.
MISSÃO: Entender o que o cliente quer e ajudá-lo a montar o melhor pedido baseando-se no HISTÓRICO DA CONVERSA.

REGRAS OBRIGATÓRIAS:
1. Comunique SEMPRE em Português de Portugal (PT-PT).
   - Use infinitivo em vez de gerúndio (ex: "a preparar" em vez de "preparando").
   - Use vocabulário local: estafeta, sumo, encomenda, ecrã, pequeno-almoço.
2. Use APENAS produtos listados na seção "PRODUTOS DISPONÍVEIS". NUNCA invente itens.
3. Validação de Quantidade Inteligente:
   - SÓ pergunte a quantidade OU para quantas pessoas após o utilizador ter escolhido um produto específico.
   - NÃO pergunte as duas coisas. Escolha a mais natural baseada no contexto:
     * Para pratos partilháveis (pizzas, sushi, combinados): pergunte "Para quantas pessoas?".
     * Para itens individuais (hambúrgueres, bebidas, sobremesas): pergunte a "Quantidade".
   - Se o utilizador já mencionou o número de pessoas no início da conversa, use essa informação para sugerir a quantidade e não pergunte novamente.
4. Gestão do Carrinho:
   - Identifique produtos pelo GID.
   - Use OBRIGATORIAMENTE a tag [[ADD_TO_CART:GID:QUANTIDADE]] para adicionar, remover ou ajustar quantidades.
   - O sistema é INCREMENTAL: a QUANTIDADE que você enviar será SOMADA ao que já existe no carrinho.
   - REGRA DE OURO: Não adicione produtos automaticamente apenas porque o utilizador escolheu um sabor. Se você vai perguntar a quantidade logo a seguir, ESPERE pela resposta dele para enviar a tag com o valor total desejado.
   - Se o utilizador já tem 1 item e diz "quero 3 no total", envie a diferença: [[ADD_TO_CART:GID:2]].
5. Finalização e Sacola:
   - Se o cliente quiser fechar ou finalizar o pedido, peça uma confirmação final.
   - Após o cliente confirmar (ex: "Sim", "Pode ser"), faça o resumo do pedido e use OBRIGATORIAMENTE a tag [[SHOW_CART]].
   - Informe que a sacola será apresentada no ecrã para que ele possa validar os detalhes, taxas e confirmar o pagamento.
   - NUNCA diga que o valor atual é o "total do pedido", refira-se como "subtotal dos produtos".
6. Seja natural, amigável e conciso (máximo 100 palavras). Nunca escreva preços, calorias ou
   alérgenos no texto — o cliente já vê essas informações no cartão do produto na tela.
7. Limite de Restaurantes: um pedido pode incluir no máximo 3 restaurantes diferentes.
   - A secção "RESTAURANTES NO PEDIDO" (quando presente) mostra quantos já estão no carrinho.
   - Ao atingir o limite ("LIMITE ATINGIDO"), ofereça e adicione APENAS produtos desses restaurantes.
   - Se o cliente pedir algo de um restaurante novo nessa situação, explique o limite com
     naturalidade e ofereça remover os itens de um dos restaurantes atuais para abrir espaço.
   - NUNCA use a tag [[ADD_TO_CART:...]] para um produto de um restaurante fora da lista quando
     o limite estiver atingido — mesmo que o cliente insista."""

            # F3: variante da system instruction para o modo function calling — mesmas
            # regras de 1 a 3 e 6, mas a regra 4/5 usa as FERRAMENTAS em vez de tags de
            # texto [[...]]. Ver nota de validação pendente junto de _TOOLS acima.
            cls._system_instruction_fc = """Você é um CONSULTOR DE VENDAS especialista em delivery de comida em Portugal.
MISSÃO: Entender o que o cliente quer e ajudá-lo a montar o melhor pedido baseando-se no HISTÓRICO DA CONVERSA.

REGRAS OBRIGATÓRIAS:
1. Comunique SEMPRE em Português de Portugal (PT-PT).
   - Use infinitivo em vez de gerúndio (ex: "a preparar" em vez de "preparando").
   - Use vocabulário local: estafeta, sumo, encomenda, ecrã, pequeno-almoço.
2. Use APENAS produtos listados na seção "PRODUTOS DISPONÍVEIS". NUNCA invente itens nem GIDs.
3. Validação de Quantidade Inteligente:
   - SÓ pergunte a quantidade OU para quantas pessoas após o utilizador ter escolhido um produto específico.
   - NÃO pergunte as duas coisas. Escolha a mais natural baseada no contexto:
     * Para pratos partilháveis (pizzas, sushi, combinados): pergunte "Para quantas pessoas?".
     * Para itens individuais (hambúrgueres, bebidas, sobremesas): pergunte a "Quantidade".
   - Se o utilizador já mencionou o número de pessoas no início da conversa, use essa informação para sugerir a quantidade e não pergunte novamente.
4. Gestão do Carrinho — use SEMPRE a ferramenta adicionar_ao_carrinho, nunca escreva a ação como texto:
   - Identifique produtos pelo GID exato do catálogo.
   - O sistema é INCREMENTAL: delta_quantidade é SOMADO ao que já existe no carrinho.
   - REGRA DE OURO: não chame a ferramenta automaticamente apenas porque o utilizador escolheu um sabor. Se você vai perguntar a quantidade logo a seguir, ESPERE a resposta dele para então chamar a ferramenta com o valor total desejado.
   - Se o utilizador já tem 1 item e diz "quero 3 no total", chame a ferramenta com delta_quantidade=2 (a diferença, não o total).
   - Depois de chamar a ferramenta, você receberá o resultado (sucesso ou erro) antes de escrever a resposta final — use esse resultado para responder com precisão; se dizer "erro", NÃO afirme ao cliente que adicionou.
5. Finalização e Sacola:
   - Se o cliente quiser fechar ou finalizar o pedido, peça uma confirmação final.
   - Após o cliente confirmar (ex: "Sim", "Pode ser"), faça o resumo do pedido e chame a ferramenta mostrar_sacola.
   - Informe que a sacola será apresentada no ecrã para que ele possa validar os detalhes, taxas e confirmar o pagamento.
   - NUNCA diga que o valor atual é o "total do pedido", refira-se como "subtotal dos produtos".
6. Destaque de Produtos: quando comparar opções, recomendar algo ou responder "o que vocês têm", chame
   sugerir_produtos com os GIDs relevantes para que apareçam como cartão na tela. Não é necessário
   chamar isto para um produto que você já adicionou via adicionar_ao_carrinho.
7. Seja natural, amigável e conciso (máximo 100 palavras). Nunca escreva preços, calorias ou alérgenos no texto — o cliente já vê essas informações no cartão do produto na tela.
8. Limite de Restaurantes: um pedido pode incluir no máximo 3 restaurantes diferentes.
   - A secção "RESTAURANTES NO PEDIDO" (quando presente) mostra quantos já estão no carrinho.
   - Ao atingir o limite ("LIMITE ATINGIDO"), chame adicionar_ao_carrinho e sugerir_produtos
     APENAS com produtos desses restaurantes.
   - Se o cliente pedir algo de um restaurante novo nessa situação, explique o limite com
     naturalidade e ofereça remover os itens de um dos restaurantes atuais para abrir espaço.
   - Se você chamar adicionar_ao_carrinho para um restaurante fora da lista e o resultado vier
     com erro=LIMITE_DE_RESTAURANTES_ATINGIDO, NÃO afirme ao cliente que adicionou — explique o
     limite usando os "restaurantes_atuais" do resultado."""

            cls._is_initialized = True
            print("✅ [Gemini] Modelo configurado com sucesso!")
            print(f"📊 [Gemini] Cache: {cls._cache.size()} entradas")

        except Exception as e:
            print(f"❌ [Gemini] Erro ao configurar: {e}")
            raise

    @classmethod
    def generate_response_stream(cls, user_message: str, context: Dict):
        """Gera resposta em stream usando Gemini"""
        if not cls.is_ready():
            cls.initialize()

        # 1. Verificar respostas estáticas (não stream, mas retorna um chunk único)
        msg_lower = user_message.lower().strip()
        if msg_lower in cls._STATIC_RESPONSES:
            yield cls._STATIC_RESPONSES[msg_lower]
            return

        # 2. Verificar limite de requisições
        if not cls._usage_monitor.can_make_request():
            yield cls._generate_fallback_response(user_message, context)
            return

        # 3. Gerar stream com Gemini com Auto-Retry
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                products = context.get("products", [])
                history_text = context.get("history_text", "")
                session_context = context.get("session_context", {})

                prompt = cls._build_prompt(user_message, products, context,
                                           history_text, session_context)
                modelo = cls._escolher_modelo(context)
                cls._ultimo_tamanho_prompt = len(prompt)

                # Tentar com os modelos disponíveis (Failover de Modelo + Chaves)
                modelos_a_tentar = [modelo, cls.MODELO_ESTAVEL] if modelo != cls.MODELO_ESTAVEL else [modelo]
                
                for mod in modelos_a_tentar:
                    for key_index, client in enumerate(cls._clients):
                        try:
                            # Usar generate_content_stream
                            stream = client.models.generate_content_stream(
                                model=mod,
                                contents=prompt,
                                config=types.GenerateContentConfig(
                                    system_instruction=cls._system_instruction,
                                    temperature=0.7,
                                    top_p=0.9,
                                    top_k=40,
                                    max_output_tokens=250,
                                    response_modalities=['TEXT'],
                                )
                            )

                            # Registrar uso (contamos como 1 req)
                            cls._usage_monitor.record_request()

                            for chunk in stream:
                                if chunk.text:
                                    yield chunk.text
                            
                            return # Sucesso absoluto

                        except Exception as e_key:
                            erro_msg = str(e_key)
                            is_quota_error = "429" in erro_msg or "RESOURCE_EXHAUSTED" in erro_msg
                            is_server_error = "503" in erro_msg or "UNAVAILABLE" in erro_msg
                            
                            if is_quota_error and key_index < len(cls._clients) - 1:
                                print(f"⚠️ [Gemini Stream] Chave {key_index+1} esgotou cota (429). Pulando para próxima...")
                                continue 
                            
                            if is_server_error:
                                if key_index < len(cls._clients) - 1:
                                    print(f"⚠️ [Gemini Stream] Modelo {mod} instável (503) na chave {key_index+1}. Tentando próxima chave...")
                                    time.sleep(0.5)
                                    continue
                                elif mod != modelos_a_tentar[-1]:
                                    print(f"🚨 [Gemini Stream] Modelo {mod} falhou em TODAS as chaves. Tentando modelo estável...")
                                    break # Sai do loop de chaves para tentar o próximo modelo
                            
                            raise e_key # Se nada resolveu, sobe para o retry temporal de 1.5s

            except Exception as e:
                erro_str = str(e)
                # 503 (UNAVAILABLE) e 429 (RESOURCE_EXHAUSTED / cota estourada) são
                # transitórios e valem retry. Sem tratar 429, cota estourada caía direto
                # no fallback genérico no meio de uma venda, sem nenhuma nova tentativa.
                is_retryable = any(code in erro_str for code in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED"))
                if is_retryable and attempt < max_retries:
                    wait_time = 1.5 * (attempt + 1)
                    print(f"⚠️ [Gemini Stream] Erro transitório detectado. Tentativa {attempt+1}/{max_retries}. Aguardando {wait_time}s... ({erro_str[:120]})")
                    time.sleep(wait_time)
                    continue

                print(f"❌ [Gemini Stream] Erro final após {attempt} retentativas: {e}")
                yield cls._generate_fallback_response(user_message, context)
                return

    @classmethod
    def generate_response(cls, user_message: str, context: Dict) -> str:
        """Gera resposta usando Gemini com cache e monitoramento"""
        if not cls.is_ready():
            cls.initialize()

        # 1. Verificar respostas estáticas
        msg_lower = user_message.lower().strip()
        if msg_lower in cls._STATIC_RESPONSES:
            print(f"💾 [Gemini] Resposta estática usada para: '{user_message}'")
            return cls._STATIC_RESPONSES[msg_lower]

        # 2. Verificar cache
        cache_key = cls._generate_cache_key(user_message, context)
        cached_response = cls._cache.get(cache_key)
        if cached_response:
            print(f"💾 [Gemini] Cache hit para: '{user_message}'")
            return cached_response

        # 3. Verificar limite de requisições
        if not cls._usage_monitor.can_make_request():
            print("⚠️  [Gemini] LIMITE DIÁRIO ATINGIDO (1500 req/dia)")
            return cls._generate_fallback_response(user_message, context)

        # 4. Gerar resposta com Gemini
        try:
            print(f"🤖 [Gemini] Gerando resposta para: '{user_message}'")

            products = context.get("products", [])
            history_text = context.get("history_text", "")
            session_context = context.get("session_context", {})

            # Construir prompt otimizado
            prompt = cls._build_prompt(user_message, products, context,
                                       history_text, session_context)
            modelo = cls._escolher_modelo(context)
            cls._ultimo_tamanho_prompt = len(prompt)

            # Chamar API (com Failover de Chaves)
            ai_response = ""
            for key_index, client in enumerate(cls._clients):
                try:
                    response = client.models.generate_content(
                        model=modelo,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=cls._system_instruction,
                            temperature=0.7,
                            top_p=0.9,
                            top_k=40,
                            max_output_tokens=250,
                            response_modalities=['TEXT'],
                        )
                    )
                    # Registrar uso e sair do loop se sucesso
                    cls._usage_monitor.record_request()
                    ai_response = cls._clean_response(response.text)
                    break
                except Exception as e_key:
                    erro_msg = str(e_key)
                    if ("429" in erro_msg or "RESOURCE_EXHAUSTED" in erro_msg or "503" in erro_msg) and key_index < len(cls._clients) - 1:
                        print(f"⚠️ [Gemini] Chave {key_index+1} falhou. Tentando próxima... ({erro_msg[:60]})")
                        continue
                    raise e_key

            if not ai_response:
                return cls._generate_fallback_response(user_message, context)

            # Salvar no cache — NUNCA cachear resposta com ação de carrinho (tags [[...]]).
            # Uma resposta com [[ADD_TO_CART:...]] cacheada seria reexecutada para outra
            # sessão com carrinho diferente que disparasse a mesma cache_key.
            if "[[" not in ai_response:
                cls._cache.set(cache_key, ai_response)

            # Log de status
            status = cls._usage_monitor.get_status()
            print(f"📊 [Gemini] Requisições hoje: {status['requests_today']}/{status['limit_daily']} ({status['percentage_used']:.1f}%)")

            return ai_response

        except Exception as e:
            print(f"❌ [Gemini] Erro ao gerar resposta: {e}")
            return cls._generate_fallback_response(user_message, context)

    @classmethod
    def generate_response_with_tools(cls, user_message: str, context: Dict, executor) -> Dict:
        """
        F3 do PLANO_EXECUCAO_IA.md — gera resposta usando function calling nativo em vez
        das tags de texto [[ADD_TO_CART:...]]/[[SHOW_CART]]. Substitui de uma vez os 8
        modos de falha silenciosa do contrato por tags (regex que não casa, GID fora do
        pool descoberto só depois do fato, tag truncada por max_output_tokens, etc.) —
        ver ANALISE_IA_PEDIDO.md seção 3.4.

        `executor` é uma função (nome_ferramenta: str, args: dict) -> dict que EXECUTA e
        VALIDA a ação (ex.: checa se o GID existe no pool, aplica teto de quantidade) e
        devolve um resultado — que é reenviado ao modelo ANTES da resposta final. Isso é
        o que impede a IA de afirmar uma ação que não foi de fato aplicada: ela só escreve
        a frase final depois de ver o resultado real da chamada.

        A execução das ferramentas (validação de GID, teto de quantidade, mutação do
        carrinho) fica em HybridAIService — GeminiSalesAgent não conhece a sessão nem o
        pool de produtos, só orquestra a conversa com o modelo.

        Retorna {"text": str, "acoes_executadas": [{"ferramenta": str, "args": dict, "resultado": dict}]}.

        NÃO USAR EM PRODUÇÃO sem antes validar o round-trip contra a API viva — ver nota
        junto de _TOOLS. Esta implementação foi testada apenas contra um dublê do cliente
        (tests/test_function_calling.py), porque os créditos da GEMINI_API_KEY do projeto
        estavam esgotados no momento em que este código foi escrito.
        """
        if not cls.is_ready():
            cls.initialize()

        products = context.get("products", [])
        history_text = context.get("history_text", "")
        session_context = context.get("session_context", {})
        prompt = cls._build_prompt(user_message, products, context, history_text, session_context)
        cls._ultimo_tamanho_prompt = len(prompt)
        modelo = cls._escolher_modelo(context)

        contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
        config = types.GenerateContentConfig(
            system_instruction=cls._system_instruction_fc,
            temperature=0.7,
            top_p=0.9,
            top_k=40,
            max_output_tokens=250,
            tools=cls._TOOLS,
        )

        acoes_executadas = []
        try:
            # Chamar API (com Failover de Chaves)
            response = None
            for key_index, client in enumerate(cls._clients):
                try:
                    response = client.models.generate_content(model=modelo, contents=contents, config=config)
                    cls._usage_monitor.record_request()
                    
                    # Se chegamos aqui, sucesso com esta chave. Agora processamos as function calls.
                    # Nota: as chamadas subsequentes (idiv-e-vinda) usarão o mesmo client.
                    for _ in range(3):
                        chamadas = response.function_calls
                        if not chamadas:
                            break

                        contents.append(response.candidates[0].content)
                        partes_resposta = []
                        for chamada in chamadas:
                            resultado = executor(chamada.name, dict(chamada.args or {}))
                            acoes_executadas.append({
                                "ferramenta": chamada.name,
                                "args": dict(chamada.args or {}),
                                "resultado": resultado,
                            })
                            partes_resposta.append(
                                types.Part.from_function_response(name=chamada.name, response=resultado)
                            )
                        contents.append(types.Content(role="user", parts=partes_resposta))
                        response = client.models.generate_content(model=modelo, contents=contents, config=config)
                        cls._usage_monitor.record_request()
                    
                    break # Sucesso total com este cliente
                except Exception as e_key:
                    erro_msg = str(e_key)
                    if ("429" in erro_msg or "RESOURCE_EXHAUSTED" in erro_msg or "503" in erro_msg) and key_index < len(cls._clients) - 1:
                        print(f"⚠️ [Gemini Tools] Chave {key_index+1} falhou. Tentando próxima... ({erro_msg[:60]})")
                        continue
                    raise e_key

            if not response:
                return {"text": cls._generate_fallback_response(user_message, context), "acoes_executadas": []}

            texto_final = cls._clean_response(response.text or "")
            return {"text": texto_final, "acoes_executadas": acoes_executadas}

        except Exception as e:
            print(f"❌ [Gemini FunctionCalling] Erro ao gerar resposta: {e}")
            return {
                "text": cls._generate_fallback_response(user_message, context),
                "acoes_executadas": acoes_executadas,
            }

    @classmethod
    def _build_prompt(
        cls,
        user_message: str,
        products: List[Dict],
        context: Dict,
        history_text: str = "",
        session_context: Optional[Dict] = None
    ) -> str:
        """Constrói prompt otimizado"""
        if session_context is None:
            session_context = {}

        # Produtos disponíveis
        products_text = ""
        if products:
            products_text = "\n\n📦 PRODUTOS DISPONÍVEIS (Use o CÓDIGO GID para adicionar ao carrinho):\n"
            for p in products[:15]:
                line = f"• [CÓDIGO: {p['gid']}] {p['name']} - € {p['price']:.2f}"

                # Detalhes complementares
                extras = []
                if p.get('category'):
                    extras.append(f"categoria: {p['category']}")
                if p.get('serves_people'):
                    extras.append(f"serve {p['serves_people']}p")
                if p.get('portion_size'):
                    extras.append(f"tamanho: {p['portion_size']}")
                if p.get('preparation_time_minutes'):
                    extras.append(f"preparo: {p['preparation_time_minutes']}min")
                elif p.get('preparation_time'):
                    extras.append(f"preparo: {p['preparation_time']}")
                if p.get('calories'):
                    extras.append(f"{p['calories']} kcal")
                if p.get('spice_level') and p['spice_level'] != 'não picante':
                    extras.append(f"🌶️ {p['spice_level']}")
                if p.get('is_popular'):
                    extras.append("⭐ popular")
                if p.get('rating'):
                    extras.append(f"avaliação: {p['rating']:.1f}")

                if extras:
                    line += f" ({', '.join(extras)})"
                products_text += line + "\n"

                # Descrição
                if p.get('description'):
                    products_text += f"  Descrição: {p['description']}\n"
                # Ingredientes
                if p.get('ingredients'):
                    products_text += f"  Ingredientes: {p['ingredients']}\n"
                # Alérgenos
                if p.get('allergens'):
                    products_text += f"  ⚠️ Alérgenos: {p['allergens']}\n"
                # Tags dietéticas
                if p.get('dietary_tags'):
                    products_text += f"  🌱 Dieta: {p['dietary_tags']}\n"
                # Recomendado para
                if p.get('recommended_for'):
                    products_text += f"  🕐 Recomendado para: {p['recommended_for']}\n"
        else:
            products_text = "\n\n⚠️ NENHUM PRODUTO DISPONÍVEL NO MOMENTO. Informe o cliente educadamente."

        # Carrinho atual do cliente (CRÍTICO para finalização)
        cart_items = context.get("cart", [])
        cart_section = ""
        if cart_items:
            cart_section = "\n\n🛒 CARRINHO ATUAL (O que o cliente já escolheu):\n"
            for item in cart_items:
                cart_section += f"• {item['name']} x{item['quantity']} - € {item['price']:.2f}\n"
            total = sum(i['price'] * i['quantity'] for i in cart_items)
            cart_section += f"Subtotal dos Produtos: € {total:.2f}"
        else:
            cart_section = "\n\n🛒 CARRINHO VAZIO."

        # Histórico da conversa
        history_section = ""
        if history_text:
            history_section = f"\n\n💬 HISTÓRICO DA CONVERSA (use para entender o contexto):\n{history_text}"

        # Contexto da sessão (pessoas, categoria atual, etc)
        ctx_parts = []
        if session_context.get("pessoas"):
            ctx_parts.append(f"pedido para {session_context['pessoas']} pessoa(s)")
        if session_context.get("categoria_atual"):
            ctx_parts.append(f"está a escolher {session_context['categoria_atual']}")
        if session_context.get("aguardando"):
            ctx_parts.append(f"a aguardar decisão sobre: {session_context['aguardando']}")
        session_section = ""
        if ctx_parts:
            session_section = f"\n\n🧠 CONTEXTO EXTRA: {' | '.join(ctx_parts)}"

        # F1.2: sinais detectados por palavra-chave em HybridAIService._detect_intent_type.
        # Antes eram calculados (intent_type, user_needs) e nunca chegavam a este prompt —
        # a IA não recebia o que o sistema já sabia sobre a intenção da mensagem.
        user_needs = context.get("user_needs", {}) or {}
        sinais = []
        if user_needs.get("has_doubt"):
            sinais.append("cliente parece indeciso entre opções")
        if user_needs.get("wants_recommendation"):
            sinais.append("cliente pediu recomendação")
        if user_needs.get("asks_quantity"):
            sinais.append("cliente perguntou sobre quantidade/porção")
        if user_needs.get("mentions_drink"):
            sinais.append("cliente mencionou bebida")
        if user_needs.get("mentions_dessert"):
            sinais.append("cliente mencionou sobremesa")
        intent_type = context.get("intent_type")
        if intent_type == "greeting":
            sinais.append("mensagem é uma saudação")
        signals_section = f"\n\n🔎 SINAIS DETECTADOS NA MENSAGEM: {' | '.join(sinais)}" if sinais else ""

        # PLANO_LIMITE_RESTAURANTES.md, Fase 3.2 — só aparece quando há pelo menos 1
        # restaurante no carrinho, para não poluir o prompt em conversas de saudação/
        # descoberta antes do primeiro item.
        restaurantes_no_pedido = context.get("restaurantes_no_pedido") or {}
        restaurant_section = ""
        if restaurantes_no_pedido:
            max_restaurantes = context.get("max_restaurantes_por_pedido", 3)
            qtd_atual = len(restaurantes_no_pedido)
            nomes = " | ".join(restaurantes_no_pedido.values())
            if qtd_atual >= max_restaurantes:
                restaurant_section = (
                    f"\n\n🏪 RESTAURANTES NO PEDIDO ({qtd_atual}/{max_restaurantes} — LIMITE ATINGIDO): {nomes}\n"
                    f"   Ofereça APENAS produtos destes {qtd_atual} restaurantes."
                )
            else:
                restaurant_section = f"\n\n🏪 RESTAURANTES NO PEDIDO ({qtd_atual}/{max_restaurantes}): {nomes}"

        # Prompt final - nota de confirmação
        order_note = ""
        if context.get("order_confirmed"):
            order_note = "\n\n⚠️ ATENÇÃO: O CLIENTE ESTÁ CONFIRMANDO O PEDIDO. Use o HISTÓRICO DA CONVERSA e o CARRINHO ATUAL acima para fazer o resumo completo e informe que os detalhes serão apresentados para o pagamento."

        prompt = f"""{products_text}{cart_section}{history_section}{session_section}{signals_section}{restaurant_section}{order_note}

═══════════════════════════════════════════════════════
CLIENTE DISSE AGORA: "{user_message}"
═══════════════════════════════════════════════════════

Responda considerando TODO o contexto acima. Seja consultivo e natural."""

        return prompt

    @classmethod
    def _clean_response(cls, response: str) -> str:
        """Limpa e valida resposta do Gemini"""
        response = response.strip()

        # Remover quebras excessivas
        response = ' '.join(response.split())

        return response

    @classmethod
    def _generate_cache_key(cls, user_message: str, context: Dict) -> str:
        """
        Gera chave de cache baseada na mensagem, produtos, carrinho e histórico recente.

        Inclui carrinho e histórico porque a resposta do Gemini depende deles: duas sessões
        diferentes com o mesmo produto no pool mas carrinhos distintos NÃO podem compartilhar
        a mesma resposta cacheada (ex.: "sim" respondido de forma diferente conforme o que
        já está no carrinho). Sem isso, uma resposta cacheada para a sessão A podia ser
        servida para a sessão B nesse estado diferente.
        """
        import hashlib

        products = context.get("products", [])
        gids = sorted(p.get("gid", "") for p in products[:6] if p.get("gid"))

        cart = context.get("cart", [])
        cart_sig = ",".join(
            f"{i.get('product_id')}x{i.get('quantity')}"
            for i in sorted(cart, key=lambda i: i.get("product_id", 0))
        )

        # Últimos ~300 caracteres do histórico bastam para diferenciar o ponto da conversa
        # sem tornar o cache inútil (praticamente todo turno teria histórico único).
        history_tail = (context.get("history_text") or "")[-300:]

        raw = f"{user_message.lower().strip()}|{cart_sig}|{history_tail}|{','.join(gids)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def _generate_fallback_response(cls, user_message: str, context: Dict) -> str:
        """Resposta de fallback adaptada para PT-PT"""
        products = context.get("products", [])

        if not products:
            return "Lamento, não encontrei produtos disponíveis de momento. Pode tentar outra pesquisa?"

        # Listar produtos de forma simples
        product_list = []
        for p in products[:3]:
            price = f"€ {p['price']:.2f}"
            product_list.append(f"{p['name']} ({price})")

        products_str = ", ".join(product_list)
        return f"Temos disponível: {products_str}. Qual prefere?"

    @classmethod
    def is_ready(cls) -> bool:
        """Verifica se modelo está pronto"""
        return cls._is_initialized and len(cls._clients) > 0

    @classmethod
    def get_usage_status(cls) -> Dict:
        """Retorna status de uso da API"""
        return cls._usage_monitor.get_status()

    @classmethod
    def get_cache_status(cls) -> Dict:
        """Retorna status do cache"""
        return {
            "entries": cls._cache.size(),
            "ttl_seconds": cls._cache._ttl
        }
