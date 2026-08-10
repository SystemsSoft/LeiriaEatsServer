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

        is_greeting = context.get("is_greeting", False)

        # ⭐ SISTEMA SIMPLIFICADO (Phi-3 entende instruções complexas melhor)
        system_prompt = """Você é um CONSULTOR DE VENDAS especialista em delivery de comida.

SUA MISSÃO:
1. ENTENDER o que o cliente quer (não empurre produtos)
2. PERGUNTAR "para quantas pessoas?"
3. SUGERIR complementos (bebida, sobremesa)

REGRAS OBRIGATÓRIAS:
✓ Use APENAS os produtos listados
✓ Máximo 50 palavras
✓ NÃO fale sobre você mesmo ou IA
✓ NÃO invente informações
✓ Seja consultivo (ajude a decidir, não force venda)

FLUXO DE VENDAS:

1. BUSCA GENÉRICA (ex: "pizza", "hambúrguer")
→ Pergunte qual sabor/tipo prefere
→ Pergunte para quantas pessoas
→ NÃO liste todos os produtos ainda

2. INDECISÃO (ex: "entre pizza e mexicana")
→ Ajude a decidir: "Pizza é para compartilhar, burrito é individual"
→ Pergunte para quantas pessoas
→ Use info para recomendar

3. PEDIDO ESPECÍFICO (ex: "pizza margherita")
→ Confirme produto com preço
→ Pergunte quantidade
→ Sugira complemento: "Quer bebida também?"

4. PRODUTO NO CARRINHO
→ Reconheça: "Pizza adicionada!"
→ Sugira: "Que tal uma Coca-Cola?"

EXEMPLOS:

Cliente: "pizza"
Você: "Temos Pizza Margherita (R$ 35) e Calabresa (R$ 38). Qual prefere? Para quantas pessoas?"

Cliente: "entre pizza e mexicana"
Você: "Pizza é para compartilhar, burrito é individual. Para quantas pessoas?"

Cliente: "margherita grande"
Você: "Pizza Margherita - R$ 35 (serve 2). Quantas quer? Adiciono bebida?"

NUNCA:
✗ Falar "sou uma IA"
✗ Agradecer por capacidades
✗ Listar produtos sem contexto
✗ Ignorar pergunta sobre pessoas"""

        # ⭐ PRODUTOS (formato limpo para Phi-3)
        products_context = ""
        if has_results and products:
            products_context = "\n\nPRODUTOS DISPONÍVEIS:\n"

            for p in products[:5]:
                # Linha simples e informativa
                parts = [f"R$ {p['price']:.2f}"]

                if p.get('serves_people'):
                    parts.append(f"serve {p['serves_people']}p")
                if p.get('preparation_time_minutes'):
                    parts.append(f"{p['preparation_time_minutes']}min")
                if p.get('is_popular'):
                    parts.append("⭐")

                products_context += f"• {p['name']} - {', '.join(parts)}\n"

                # Descrição curta
                if p.get('description'):
                    desc = p['description'][:80]
                    products_context += f"  {desc}\n"

                # Ingredientes (importante para perguntas)
                if p.get('ingredients'):
                    ing = p['ingredients'][:100]
                    products_context += f"  Ingredientes: {ing}\n"

                products_context += "\n"
        elif not is_greeting:
            products_context = "\n\nNenhum produto encontrado.\n"

        # Carrinho
        cart_context = ""
        if cart:
            cart_context += "\n\nCARRINHO:\n"
            for item in cart:
                name = item.get('name', 'Produto')
                qty = item.get('quantity', 1)
                cart_context += f"• {name} x{qty}\n"
            cart_context += "\n💡 Sugira complementos!\n"

        # ⭐ FORMATO PHI-3 (específico - <|system|> <|user|> <|assistant|>)
        prompt = f"""<|system|>
{system_prompt}<|end|>
<|user|>
{products_context}{cart_context}

Cliente: "{user_message}"

Responda seguindo as REGRAS e EXEMPLOS acima. Seja natural e consultivo.<|end|>
<|assistant|>
"""

        return prompt

    @classmethod
    def _clean_response(cls, response: str) -> str:
        """
        Limpa e valida resposta do modelo Phi-3
        Phi-3 alucina menos, mas ainda precisa de limpeza básica
        """
        # Remover espaços extras
        response = response.strip()

        # Remover tags Phi-3 específicas
        for tag in ['<|end|>', '<|system|>', '<|user|>', '<|assistant|>']:
            response = response.replace(tag, '')

        # Parar em quebra de linha dupla (pode ser início de alucinação)
        if '\n\n' in response:
            response = response.split('\n\n')[0].strip()

        # ⭐ DETECTOR DE ALUCINAÇÕES (mais simples que TinyLlama)
        hallucination_keywords = [
            "sou uma ia",
            "sou um modelo",
            "fui treinado",
            "fui programado",
            "como assistente",
            "minha capacidade",
            "capacidade com escritora",
            "programadora",
            "agradeço pela",
            "desejando ser"
        ]

        response_lower = response.lower()

        # Se detectar alucinação, bloquear
        if any(keyword in response_lower for keyword in hallucination_keywords):
            print(f"⚠️  [Phi-3] Alucinação detectada: '{response[:80]}...'")
            return "Desculpe, pode reformular? O que você gostaria de pedir? 😊"

        # Validar tamanho (muito curta = erro)
        if len(response) < 10:
            print(f"⚠️  [Phi-3] Resposta muito curta: '{response}'")
            return "O que você gostaria de pedir?"

        # Limitar tamanho (muito longa = possível divagação)
        if len(response) > 250:
            sentences = response.split('.')
            if len(sentences) > 3:
                response = '. '.join(sentences[:3]) + '.'
            else:
                response = response[:247] + '...'

        # Remover múltiplos espaços
        response = ' '.join(response.split())

        # Remover caracteres estranhos no início
        response = response.lstrip('.-:;,!? ')

        # Garantir pontuação final
        if response and response[-1] not in '.!?':
            if response[-1].isalnum():
                response += '.'

        return response.strip()

    @classmethod
    def is_ready(cls) -> bool:
        """Verifica se o modelo está carregado e pronto"""
        return cls._is_initialized and cls._model is not None

