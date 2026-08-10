"""
Phi-3 Sales Service
Modelo de IA conversacional Microsoft Phi-3-Mini (3.8B) para interação de vendas consultivas
Muito mais inteligente que TinyLlama, segue instruções complexas sem alucinar
"""
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch
from typing import Dict, List, Optional


class TinyLlamaSalesAgent:
    """
    Agente de vendas baseado em Microsoft Phi-3-Mini 3.8B
    Modelo otimizado para seguir instruções complexas e conversação natural
    Requer ~8GB RAM mas oferece qualidade muito superior
    """
    _model = None
    _tokenizer = None
    _is_initialized = False

    @classmethod
    def initialize(cls):
        """Carrega o modelo Phi-3-Mini uma vez na inicialização"""
        if cls._is_initialized:
            return

        try:
            print("🤖 [Phi-3] Iniciando carregamento do modelo Microsoft Phi-3-Mini...")
            print("⏳ [Phi-3] Este processo pode levar 2-3 minutos na primeira vez...")

            # ⭐ MICROSOFT PHI-3-MINI 3.8B
            model_id = "microsoft/Phi-3-mini-4k-instruct"

            # Carregar tokenizer
            print("📚 [Phi-3] Carregando tokenizer...")
            cls._tokenizer = AutoTokenizer.from_pretrained(
                model_id,
                trust_remote_code=True
            )

            # Carregar modelo otimizado para CPU
            print("🧠 [Phi-3] Carregando modelo (3.8B parâmetros - ~7.5GB)...")
            cls._model = AutoModelForCausalLM.from_pretrained(
                model_id,
                device_map="cpu",
                torch_dtype=torch.float32,  # Float32 para CPU (compatibilidade Mac)
                trust_remote_code=True,
                low_cpu_mem_usage=True
            )

            cls._is_initialized = True
            print("✅ [Phi-3] Modelo carregado com sucesso!")
            print("💡 [Phi-3] Phi-3-Mini é 3x maior que TinyLlama e muito mais inteligente!")

        except Exception as e:
            print(f"❌ [Phi-3] Erro ao carregar modelo: {e}")
            print("💡 Se o erro for de memória, feche outros aplicativos e tente novamente.")
            raise

    @classmethod
    def generate_response(
        cls,
        user_message: str,
        context: Dict
    ) -> str:
        """
        Gera resposta conversacional usando Phi-3-Mini

        Args:
            user_message: Mensagem do usuário
            context: Dicionário com:
                - products: Lista de produtos encontrados pela busca semântica
                - cart: Lista de itens no carrinho
                - user_query: Query original do usuário
                - has_results: Se encontrou produtos

        Returns:
            Resposta em texto natural consultivo
        """
        # Verificar se modelo está pronto
        if not cls.is_ready():
            print("⚠️  [Phi-3] Modelo não inicializado, tentando inicializar...")
            try:
                cls.initialize()
            except Exception as e:
                print(f"❌ [Phi-3] Falha na inicialização: {e}")
                raise Exception("Phi-3 não está pronto e não pôde ser inicializado")

        products = context.get("products", [])
        cart = context.get("cart", [])
        has_results = context.get("has_results", False)

        # Construir prompt baseado no contexto
        prompt = cls._build_prompt(user_message, products, cart, has_results, context)

        try:
            # Tokenizar entrada (Phi-3 suporta 4k tokens)
            inputs = cls._tokenizer(
                prompt,
                return_tensors="pt",
                truncation=True,
                max_length=2048  # Phi-3 aguenta contexto maior
            ).to(cls._model.device)

            # Gerar resposta (Phi-3 funciona melhor com parâmetros mais conservadores)
            with torch.no_grad():
                outputs = cls._model.generate(
                    **inputs,
                    max_new_tokens=100,  # Respostas concisas (~60 palavras)
                    temperature=0.6,  # Phi-3 funciona melhor com temp mais baixa
                    top_p=0.9,
                    do_sample=True,
                    pad_token_id=cls._tokenizer.eos_token_id,
                    eos_token_id=cls._tokenizer.eos_token_id,
                    repetition_penalty=1.2  # Phi-3 precisa de menos penalidade
                )

            # Decodificar apenas a resposta gerada (sem o prompt)
            response = cls._tokenizer.decode(
                outputs[0][inputs['input_ids'].shape[1]:],
                skip_special_tokens=True
            )

            # Limpar resposta
            response = cls._clean_response(response)

            return response

        except Exception as e:
            print(f"❌ [Phi-3] Erro ao gerar resposta: {e}")
            import traceback
            traceback.print_exc()
            raise

    @classmethod
    def _build_prompt(
        cls,
        user_message: str,
        products: List[Dict],
        cart: List[Dict],
        has_results: bool,
        context: Optional[Dict] = None
    ) -> str:
        """
        Prompt otimizado para Phi-3-Mini
        Phi-3 é muito melhor em seguir instruções e entende contexto complexo
        """

        if context is None:
            context = {}

        intent_type = context.get("intent_type", "product_search")
        user_needs = context.get("user_needs", {})
        is_greeting = context.get("is_greeting", False)

        # ⭐ SISTEMA SIMPLIFICADO (Phi-3 entende instruções complexas melhor)
        system_prompt = """Você é um CONSULTOR DE VENDAS especialista em delivery de comida.

═══════════════════════════════════════════════════════
MISSÃO PRINCIPAL:
═══════════════════════════════════════════════════════
1. ENTENDER exatamente o que o cliente quer
2. PERGUNTAR para quantas pessoas é o pedido
3. SUGERIR produtos complementares (bebida, sobremesa, acompanhamentos)
4. FACILITAR a decisão do cliente com perguntas estratégicas
5. FOCAR 100% no pedido - NUNCA fale sobre você mesmo

═══════════════════════════════════════════════════════
REGRAS OBRIGATÓRIAS:
═══════════════════════════════════════════════════════
✓ Use APENAS os produtos listados abaixo
✓ Máximo 60 palavras por resposta
✓ NÃO invente informações que não estão nos produtos
✓ NÃO fale sobre você, sua programação ou capacidades
✓ SEMPRE pergunte quantidade de pessoas quando relevante
✓ SUGIRA produtos relacionados quando apropriado
✓ Seja amigável mas direto ao ponto

═══════════════════════════════════════════════════════
FLUXO DE VENDAS CONSULTIVAS:
═══════════════════════════════════════════════════════

SITUAÇÃO 1: SAUDAÇÃO
Cliente: "oi" / "olá" / "bom dia"
→ Cumprimente caloroso
→ Pergunte o que deseja

SITUAÇÃO 2: BUSCA GENÉRICA (cliente não sabe o que quer)
Cliente: "pizza" / "hambúrguer" / "comida"
→ NÃO liste produtos ainda
→ Faça 2-3 perguntas estratégicas:
  • "Qual sabor/tipo prefere?"
  • "Para quantas pessoas?"
  • "Prefere algo leve ou pesado?"
  • "Quanto tempo tem?"
→ Use respostas para refinar sugestão

SITUAÇÃO 3: INDECISÃO (cliente entre opções)
Cliente: "estou entre pizza e mexicana"
→ NÃO empurre produto
→ Ajude a decidir com perguntas sobre:
  • Contexto (almoço/jantar/lanche?)
  • Número de pessoas
  • Preferência (leve/pesado, rápido/elaborado)
→ Baseie recomendação nas respostas

SITUAÇÃO 4: PEDIDO ESPECÍFICO
Cliente: "quero pizza margherita grande"
→ Confirme o produto com preço e detalhes
→ Pergunte quantidade
→ SUGIRA complementos:
  • "Quer adicionar uma bebida?"
  • "Que tal uma sobremesa?"
  • "Posso incluir batata frita?"

SITUAÇÃO 5: PRODUTO NO CARRINHO
Carrinho: Pizza Margherita
→ Reconheça o pedido
→ SUGIRA complementos que combinam
→ Pergunte se quer mais alguma coisa

SITUAÇÃO 6: PERGUNTA SOBRE PRODUTO
Cliente: "tem cebola na pizza?"
→ Use informações de ingredientes
→ Se não souber, seja honesto: "Deixa eu verificar"

═══════════════════════════════════════════════════════
TÉCNICAS DE VENDAS:
═══════════════════════════════════════════════════════

UPSELLING (vender mais do mesmo):
• Cliente pediu 1 pizza para 4 pessoas → Sugira 2 pizzas
• Cliente quer individual → Sugira tamanho maior se economizar

CROSS-SELLING (produtos complementares):
• Pizza → Bebida + sobremesa
• Hambúrguer → Batata frita + refrigerante
• Prato principal → Entrada + bebida

PERGUNTAS ESTRATÉGICAS:
• "Para quantas pessoas?" → Ajuda dimensionar pedido
• "É para agora ou pode esperar?" → Produtos rápidos vs elaborados
• "Prefere algo leve ou pesado?" → Ajuda escolher categoria
• "Alguma restrição alimentar?" → Mostra cuidado

═══════════════════════════════════════════════════════
EXEMPLOS DE CONVERSAS PERFEITAS:
═══════════════════════════════════════════════════════

Exemplo 1: Saudação
─────────────────────
Cliente: "oi"
Você: "Olá! 😊 O que você gostaria de pedir hoje?"

Exemplo 2: Busca Genérica
─────────────────────
Cliente: "pizza"
Produtos: Pizza Margherita R$ 35,00 (2p), Pizza Calabresa R$ 38,00 (2p)
Você: "Temos pizzas deliciosas! Qual sabor prefere: Margherita ou Calabresa? É para quantas pessoas?"

Exemplo 3: Indecisão
─────────────────────
Cliente: "estou entre pizza e mexicana"
Produtos: Pizza Margherita, Burrito Mexicano
Você: "Entendi! 🤔 Prefere algo para compartilhar (pizza) ou individual (burrito)? É para quantas pessoas?"

Exemplo 4: Pedido Específico
─────────────────────
Cliente: "quero pizza margherita"
Produtos: Pizza Margherita R$ 35,00 (serve 2 pessoas)
Você: "Perfeito! Pizza Margherita - R$ 35,00 (serve 2 pessoas). Quantas você quer? Posso adicionar uma bebida também?"

Exemplo 5: Upselling
─────────────────────
Cliente: "1 pizza para 4 pessoas"
Você: "Uma pizza serve 2 pessoas. Para 4 pessoas recomendo 2 pizzas. Qual você prefere?"

Exemplo 6: Cross-selling
─────────────────────
Cliente: "pode adicionar a pizza"
Carrinho: Pizza Margherita
Você: "Pizza adicionada! 🍕 Quer incluir uma bebida e sobremesa? Temos Coca-Cola 2L por R$ 8,00."

Exemplo 7: Pergunta sobre Produto
─────────────────────
Cliente: "tem cebola na pizza?"
Produto: Pizza Portuguesa (ingredientes: mussarela, presunto, ovos, cebola, azeitona)
Você: "Sim, a Pizza Portuguesa tem cebola. Se preferir sem cebola, temos a Margherita (molho, mussarela, manjericão)."

Exemplo 8: Finalizando Pedido
─────────────────────
Cliente: "só isso mesmo"
Carrinho: Pizza Margherita, Coca-Cola 2L
Você: "Pedido confirmado: Pizza Margherita + Coca-Cola 2L. Total: R$ 43,00. Mais alguma coisa?"

═══════════════════════════════════════════════════════
O QUE NUNCA FAZER:
═══════════════════════════════════════════════════════
✗ Falar "sou uma IA" ou "fui programado"
✗ Agradecer por suas capacidades
✗ Mencionar programação ou linguística
✗ Listar todos os produtos sem contexto
✗ Responder sem perguntar para quantas pessoas
✗ Ignorar oportunidade de sugerir complementos
✗ Ser robotizado ou muito formal

═══════════════════════════════════════════════════════
AGORA É SUA VEZ - RESPONDA COMO NOS EXEMPLOS ACIMA:
═══════════════════════════════════════════════════════"""

        # ⭐ CONTEXTO COMPLETO DOS PRODUTOS
        products_context = ""
        if has_results and products:
            products_context = "\n\n📦 PRODUTOS DISPONÍVEIS NO CARDÁPIO:\n"
            products_context += "─────────────────────────────────────\n"

            for p in products[:5]:
                # Linha principal: Nome - Preço - Serve X pessoas
                parts = [f"R$ {p['price']:.2f}"]

                if p.get('serves_people'):
                    parts.append(f"serve {p['serves_people']} pessoa(s)")
                if p.get('portion_size'):
                    parts.append(p['portion_size'])
                if p.get('preparation_time_minutes'):
                    parts.append(f"pronto em {p['preparation_time_minutes']}min")
                if p.get('is_popular'):
                    parts.append("⭐ POPULAR")

                products_context += f"• {p['name']}\n"
                products_context += f"  {' | '.join(parts)}\n"

                # Descrição
                if p.get('description'):
                    desc = p['description'][:100]
                    products_context += f"  {desc}\n"

                # Ingredientes (importante para perguntas)
                if p.get('ingredients'):
                    ing = p['ingredients'][:120]
                    products_context += f"  Ingredientes: {ing}\n"

                # Tags úteis
                tags = []
                if p.get('spice_level') and 'não picante' not in str(p.get('spice_level', '')).lower():
                    tags.append(f"🌶️ {p['spice_level']}")
                if p.get('dietary_tags'):
                    diet_tags = p['dietary_tags'].split(',')[:2]
                    tags.extend([f"🌱 {t.strip()}" for t in diet_tags])
                if p.get('allergens'):
                    allergen_list = p['allergens'].split(',')[:2]
                    tags.extend([f"⚠️ {a.strip()}" for a in allergen_list])

                if tags:
                    products_context += f"  {' | '.join(tags)}\n"

                products_context += "\n"
        elif not is_greeting:
            products_context = "\n\n❌ Nenhum produto encontrado para essa busca.\n"

        # ⭐ CARRINHO (importante para cross-selling)
        cart_context = ""
        if cart:
            cart_context += "\n\n🛒 CARRINHO ATUAL DO CLIENTE:\n"
            cart_context += "─────────────────────────────────────\n"
            total = 0
            for item in cart:
                name = item.get('name', 'Produto')
                qty = item.get('quantity', 1)
                price = item.get('price', 0)
                subtotal = price * qty
                total += subtotal
                cart_context += f"• {name} x{qty} = R$ {subtotal:.2f}\n"
            cart_context += f"─────────────────────────────────────\n"
            cart_context += f"TOTAL: R$ {total:.2f}\n"
            cart_context += "\n💡 OPORTUNIDADE: Sugira produtos complementares!\n"

        # ⭐ CONTEXTO DAS NECESSIDADES DETECTADAS
        needs_context = ""
        if user_needs and any(user_needs.values()):
            needs_context += "\n\n🎯 CONTEXTO DO CLIENTE:\n"
            needs_context += "─────────────────────────────────────\n"

            if user_needs.get("has_doubt"):
                needs_context += "• Cliente está INDECISO entre opções → Ajude a decidir com perguntas\n"
            if user_needs.get("wants_recommendation"):
                needs_context += "• Cliente quer RECOMENDAÇÃO → Sugira produtos populares\n"
            if user_needs.get("asks_quantity"):
                needs_context += "• Perguntou sobre QUANTIDADE/PORÇÃO → Pergunte para quantas pessoas\n"
            if user_needs.get("mentions_drink"):
                needs_context += "• Mencionou BEBIDA → Oportunidade de cross-sell\n"
            if user_needs.get("mentions_dessert"):
                needs_context += "• Mencionou SOBREMESA → Oportunidade de cross-sell\n"

        # ⭐ PROMPT FINAL ULTRA-ESTRUTURADO
        prompt = f"""<|system|>
{system_prompt}</s>
<|user|>
{products_context}{cart_context}{needs_context}

═══════════════════════════════════════════════════════
MENSAGEM DO CLIENTE: "{user_message}"
═══════════════════════════════════════════════════════

Analise o contexto acima e responda seguindo EXATAMENTE as regras e exemplos.
Foque em: 1) Entender o que quer, 2) Perguntar quantas pessoas, 3) Sugerir complementos.
Máximo 60 palavras. Seja natural e consultivo.</s>
<|assistant|>
"""

        return prompt

    @classmethod
    def _clean_response(cls, response: str) -> str:
        """
        Limpa e valida resposta do modelo
        Detecta e bloqueia alucinações comuns
        """
        # Remover espaços extras
        response = response.strip()

        # Parar em quebra de linha dupla (geralmente início de alucinação)
        if '\n\n' in response:
            response = response.split('\n\n')[0].strip()

        # Remover tags especiais que possam ter vazado
        for tag in ['<|', '|>', '</s>', '<s>', '<|user|>', '<|assistant|>', '<|system|>']:
            response = response.replace(tag, '')

        # ⭐ DETECTOR DE ALUCINAÇÕES (palavras-chave suspeitas)
        hallucination_keywords = [
            # Auto-referências (IA falando sobre si)
            "sou uma ia",
            "sou um modelo",
            "fui treinado",
            "fui programado",
            "como assistente",
            "como modelo",
            "minha capacidade",
            "capacidade com escritora",
            "programadora de aplicações",
            "aplicações eletrônicas",
            "linguística",

            # Agradecimentos estranhos
            "agradeço pela",
            "obrigado pela",
            "grato pela",
            "desejando ser",

            # Disclaimers desnecessários
            "não posso ajudar",
            "desculpe mas não posso",
            "não tenho acesso",
            "não sei se posso",

            # Frases robóticas
            "processando sua solicitação",
            "analisando seu pedido",
            "verificando disponibilidade"
        ]

        response_lower = response.lower()

        # Contar quantas palavras-chave de alucinação aparecem
        hallucination_count = sum(1 for keyword in hallucination_keywords if keyword in response_lower)

        # Se detectar 1+ alucinação clara, bloquear e retornar fallback
        if hallucination_count >= 1:
            print(f"⚠️  [TinyLlama] Alucinação detectada: '{response[:80]}...'")
            return "Desculpe, pode reformular sua pergunta? Estou aqui para ajudar com seu pedido! 😊"

        # ⭐ VALIDAR SE A RESPOSTA FAZ SENTIDO
        # Muito curta (menos de 10 caracteres) = provavelmente erro
        if len(response) < 10:
            print(f"⚠️  [TinyLlama] Resposta muito curta: '{response}'")
            return "Não entendi direito. Pode reformular? O que você gostaria de pedir?"

        # Muito longa (mais de 250 caracteres) = possível divagação
        if len(response) > 250:
            # Limitar a 2-3 frases
            sentences = response.split('.')
            if len(sentences) > 3:
                response = '. '.join(sentences[:3]) + '.'
            else:
                response = response[:247] + '...'

        # Remover múltiplos espaços
        response = ' '.join(response.split())

        # Remover caracteres especiais no início
        response = response.lstrip('.-:;,!? ')

        # Garantir que termina com pontuação
        if response and response[-1] not in '.!?':
            # Adicionar ponto se terminar com palavra completa
            if response[-1].isalnum():
                response += '.'

        return response.strip()

    @classmethod
    def is_ready(cls) -> bool:
        """Verifica se o modelo está carregado e pronto"""
        return cls._is_initialized and cls._model is not None

