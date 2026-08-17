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
    _model = None
    _is_initialized = False
    _cache = GeminiCache(ttl_seconds=1800)  # Cache de 30 minutos
    _usage_monitor = GeminiUsageMonitor()

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
        """Inicializa o cliente Gemini"""
        if cls._is_initialized:
            return

        try:
            print("🤖 [Gemini] Configurando API...")

            # Configurar cliente com API key
            cls._model = genai.Client(api_key=settings.GEMINI_API_KEY)

            cls._is_initialized = True
            print("✅ [Gemini] Modelo configurado com sucesso!")
            print(f"📊 [Gemini] Cache: {cls._cache.size()} entradas")

        except Exception as e:
            print(f"❌ [Gemini] Erro ao configurar: {e}")
            raise

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

            # Chamar API com nova sintaxe
            response = cls._model.models.generate_content(
                model='gemini-flash-lite-latest',
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    top_p=0.9,
                    top_k=40,
                    max_output_tokens=200,
                    response_modalities=['TEXT'],
                )
            )

            # Registrar uso
            cls._usage_monitor.record_request()

            # Limpar e validar resposta
            ai_response = cls._clean_response(response.text)

            # Salvar no cache
            cls._cache.set(cache_key, ai_response)

            # Log de status
            status = cls._usage_monitor.get_status()
            print(f"📊 [Gemini] Requisições hoje: {status['requests_today']}/{status['limit_daily']} ({status['percentage_used']:.1f}%)")

            return ai_response

        except Exception as e:
            print(f"❌ [Gemini] Erro ao gerar resposta: {e}")
            return cls._generate_fallback_response(user_message, context)

    @classmethod
    def _build_prompt(
        cls,
        user_message: str,
        products: List[Dict],
        context: Dict,
        history_text: str = "",
        session_context: Optional[Dict] = None
    ) -> str:
        """
        Constrói prompt otimizado para Gemini
        Inclui regras específicas para o idioma de Portugal
        """
        if session_context is None:
            session_context = {}

        # Sistema base
        system = """Você é um CONSULTOR DE VENDAS especialista em delivery de comida em Portugal.

MISSÃO: Entender o que o cliente quer e ajudá-lo a montar o melhor pedido baseando-se no HISTÓRICO DA CONVERSA.

REGRAS OBRIGATÓRIAS:
1. Comunique SEMPRE em Português de Portugal (PT-PT).
   - Evite gerúndios onde o infinitivo for mais natural (ex: use "estou a preparar" em vez de "estou preparando").
   - Use vocabulário local (ex: "estafeta" em vez de "entregador", "sumo" em vez de "suco", "encomenda" ou "pedido", "ecrã" em vez de "tela").
   - Trate o cliente de forma educada, preferencialmente na terceira pessoa (ex: "Como posso ajudar?" ou "O que gostaria?" em vez do uso excessivo de "você").
2. Use APENAS e EXCLUSIVAMENTE os produtos listados na seção "PRODUTOS DISPONÍVEIS" abaixo — NUNCA invente, mencione ou sugira produtos que não estejam nessa lista.
3. Se o cliente menciona uma categoria (pizza, hambúrguer, salada): liste TODAS as opções daquela categoria que existem na lista de produtos.
4. Se o cliente pede "outro sabor" ou "outra opção": liste outras opções que estão na lista de produtos.
5. Use o HISTÓRICO DA CONVERSA para entender TODO o contexto — nunca esqueça o que foi combinado antes.
6. Use o HISTÓRICO DA CONVERSA para saber o que já foi pedido e sugerir complementos coerentes.
7. SEMPRE pergunte "para quantas pessoas?" se não souber a quantidade.
8. Após escolher o prato principal, verifique se há bebidas ou sobremesas na lista e sugira-as. Se não houver, NÃO mencione.
9. Se o cliente pedir algo que não está na lista, informe: "Não temos essa opção disponível de momento."
10. Quando o cliente pedir DETALHES de um produto, use TODOS os campos disponíveis: descrição, ingredientes, alérgenos, calorias, tempo de preparo, tags dietéticas, nível de picância, porção — mostre o que for relevante.
11. Se o produto tiver alérgenos e o cliente tiver restrição alimentar, avise proativamente.
12. Gestão do Carrinho (ADICIONAR PRODUTOS):
    - Se o cliente quiser pedir, encomendar ou adicionar algo (ex: "quero uma pizza", "pode adicionar 2 sumos"):
        - Identifique o produto na lista "PRODUTOS DISPONÍVEIS" pelo seu ID numérico.
        - OBRIGATORIAMENTE inclua a tag [[ADD_TO_CART:ID:QUANTIDADE]] na sua resposta para cada item solicitado.
        - Substitua ID pelo número do produto e QUANTIDADE pelo número pedido (ou 1 se não especificado).
        - Exemplo: "Com certeza! Vou adicionar 2 Pizzas Calabresa ao seu carrinho. 😊 [[ADD_TO_CART:123:2]]"
        - Se não tiver certeza absoluta do produto, peça esclarecimentos antes de adicionar a tag.
        - SEMPRE que disser que adicionou algo, a tag DEVE estar presente.
13. Fluxo de Finalização e Confirmação:
    - Se o cliente demonstrar desejo de finalizar (ex: "quero fechar", "quanto fica", "pode terminar"):
        - Verifique a seção "🛒 CARRINHO ATUAL" abaixo.
        - Se o carrinho estiver VAZIO: Informe educadamente que o carrinho está vazio e convide-o a escolher algo da lista de produtos.
        - Se o carrinho TIVER ITENS: Peça uma confirmação explícita (ex: "Deseja que eu finalize o seu pedido agora?"). NÃO inclua a tag [[CONFIRM_ORDER]] nesta fase.
    - Se o cliente responder POSITIVAMENTE à sua pergunta de confirmação (ex: "Sim", "Pode ser", "Com certeza"):
        - Use os dados da seção "🛒 CARRINHO ATUAL" para fazer um RESUMO completo (itens e total).
        - Informe que os detalhes serão apresentados no ecrã para pagamento.
        - OBRIGATORIAMENTE inclua a tag [[CONFIRM_ORDER]] no final da sua resposta.
    - Exemplo de resposta final: "Perfeito! O seu pedido está confirmado: 🛒\n• Pizza Calabresa x1 - € 15,00\nTotal: € 15,00\n\nVou apresentar os detalhes para que possa efetuar o pagamento! 💳 [[CONFIRM_ORDER]]"
    - Se o cliente responder NEGATIVAMENTE, continue a conversa normalmente.
14. NUNCA diga que o pedido foi enviado para a cozinha — isso é feito apenas após o pagamento pelo sistema.
15. Seja natural, amigável e conciso (máximo 100 palavras, ou mais se o cliente pedir detalhes).
16. NÃO fale sobre você mesmo.

EXEMPLOS DE COMO RESPONDER (use nomes reais da lista):

Cliente: "quero uma pizza"
Você: "Temos [X] opções: 🍕 [Nome1] (€ XX), [Nome2] (€ XX). Qual prefere? É para quantas pessoas?"

Cliente: "calabresa" (após perguntar sobre pizza)
Você: "Ótima escolha! Pizza Calabresa anotada! 🍕 [[ADD_TO_CART:123:1]] Deseja adicionar uma bebida ou sobremesa?"

Cliente: "pode fechar o pedido" (se tiver itens no carrinho)
Você: "Com certeza! Deseja que eu finalize a sua encomenda agora? 😊"

Cliente: "sim" (após perguntar sobre finalizar)
Você: "Perfeito! O seu pedido está confirmado: 🛒\n• Pizza Calabresa x1 - € 15,00\nTotal: € 15,00\n\nVou apresentar os detalhes para que possa efetuar o pagamento! 💳 [[CONFIRM_ORDER]]"
"""

        # Produtos disponíveis
        products_text = ""
        if products:
            products_text = "\n\n📦 PRODUTOS DISPONÍVEIS (Use o ID para adicionar ao carrinho):\n"
            for p in products[:15]:
                line = f"• [ID: {p['id']}] {p['name']} - € {p['price']:.2f}"

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
            cart_section += f"Total: € {total:.2f}"
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

        # Prompt final - nota de confirmação
        order_note = ""
        if context.get("order_confirmed"):
            order_note = "\n\n⚠️ ATENÇÃO: O CLIENTE ESTÁ CONFIRMANDO O PEDIDO. Use o HISTÓRICO DA CONVERSA e o CARRINHO ATUAL acima para fazer o resumo completo e informe que os detalhes serão apresentados para o pagamento."

        prompt = f"""{system}
{products_text}{cart_section}{history_section}{session_section}{order_note}

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
        """Gera chave de cache baseada na mensagem e produtos"""
        # Simplificar para cache mais eficiente
        products = context.get("products", [])
        product_ids = sorted([p.get('id', 0) for p in products[:3]])

        cache_key = f"{user_message.lower().strip()}_{','.join(map(str, product_ids))}"
        return cache_key

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
        return cls._is_initialized and cls._model is not None

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