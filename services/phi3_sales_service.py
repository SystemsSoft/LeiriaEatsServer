"""
Phi-3 Sales Service
Modelo Microsoft Phi-3-Mini (3.8B) para conversação consultiva avançada
Substitui TinyLlama com modelo muito mais inteligente
"""
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
import torch
from typing import Dict, List, Optional
import os
import gc


class Phi3SalesAgent:
    """
    Agente de vendas baseado em Microsoft Phi-3-Mini 3.8B
    Modelo otimizado para seguir instruções complexas de vendas consultivas
    """
    _model = None
    _tokenizer = None
    _is_initialized = False

    @classmethod
    def initialize(cls):
        """Carrega o modelo Phi-3-Mini uma vez na inicialização com otimizações de memória"""
        if cls._is_initialized:
            return

        try:
            print("🤖 [Phi-3] Iniciando carregamento do modelo Microsoft Phi-3-Mini 3.8B...")
            print("    🧠 Usando quantização 8-bit para economizar RAM (~2-3GB ao invés de 8GB)")

            # Limpar memória antes de carregar
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            # ⭐ MODELO PHI-3 (3.8B - muito melhor que TinyLlama 1.1B)
            model_id = "microsoft/Phi-3-mini-4k-instruct"

            print("📚 [Phi-3] Carregando tokenizer...")
            cls._tokenizer = AutoTokenizer.from_pretrained(
                model_id,
                trust_remote_code=True
            )

            print("🧠 [Phi-3] Carregando modelo com quantização 8-bit...")
            print("    💾 Isto permite usar swap quando RAM não for suficiente")
            print("    ⏳ Pode levar 3-5 minutos dependendo da velocidade do disco...")

            # Configurar quantização 8-bit para economizar RAM
            # Reduz uso de ~7.5GB para ~2-3GB
            quantization_config = BitsAndBytesConfig(
                load_in_8bit=True,
                llm_int8_enable_fp32_cpu_offload=True,  # Permite usar swap
                llm_int8_threshold=6.0
            )

            # Tentar carregar com quantização
            try:
                cls._model = AutoModelForCausalLM.from_pretrained(
                    model_id,
                    quantization_config=quantization_config,
                    device_map="cpu",
                    trust_remote_code=True,
                    low_cpu_mem_usage=True,  # Otimiza uso de RAM
                    max_memory={0: "2GB", "cpu": "6GB"}  # Limita uso de memória
                )
                print("    ✅ Modelo carregado com quantização 8-bit")
            except Exception as quant_error:
                print(f"    ⚠️  Quantização falhou: {quant_error}")
                print("    🔄 Tentando carregar sem quantização com otimizações...")

                # Fallback: carregar sem quantização mas com otimizações
                cls._model = AutoModelForCausalLM.from_pretrained(
                    model_id,
                    device_map="cpu",
                    torch_dtype=torch.float16,  # Float16 economiza 50% de RAM vs float32
                    trust_remote_code=True,
                    low_cpu_mem_usage=True,
                    offload_folder="offload",  # Usar disco se necessário
                    offload_state_dict=True
                )
                print("    ✅ Modelo carregado em float16 com offload")

            cls._is_initialized = True
            print("✅ [Phi-3] Modelo carregado com sucesso!")
            print("    🎯 Pronto para conversação consultiva inteligente!")
            print("    💾 Sistema configurado para usar swap quando necessário")

            # Limpar memória novamente após carregamento
            gc.collect()

        except Exception as e:
            print(f"❌ [Phi-3] Erro ao carregar modelo: {e}")
            print("    💡 Dicas para resolver:")
            print("       • Certifique-se de ter ~3-4GB RAM + swap disponível")
            print("       • Feche outros programas para liberar memória")
            print("       • Verifique se tem espaço em disco (modelo usa ~7.5GB)")
            print("       • Execute: pip install --upgrade transformers bitsandbytes accelerate")

            import traceback
            traceback.print_exc()
            raise

    @classmethod
    def generate_response(
        cls,
        user_message: str,
        context: Dict
    ) -> str:
        """
        Gera resposta conversacional usando Phi-3

        Args:
            user_message: Mensagem do usuário
            context: Dicionário com produtos, carrinho, intent, etc.

        Returns:
            Resposta em texto natural
        """
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

        # Construir prompt otimizado para Phi-3
        prompt = cls._build_prompt(user_message, products, cart, has_results, context)

        try:
            # Tokenizar entrada
            inputs = cls._tokenizer(
                prompt,
                return_tensors="pt",
                truncation=True,
                max_length=1024  # Phi-3 suporta 4k, mas usamos 1k para velocidade
            ).to(cls._model.device)

            # Gerar resposta com parâmetros otimizados para Phi-3
            with torch.no_grad():
                outputs = cls._model.generate(
                    **inputs,
                    max_new_tokens=80,  # Respostas concisas
                    temperature=0.6,  # Phi-3 funciona melhor com temperatura baixa
                    top_p=0.9,
                    do_sample=True,
                    pad_token_id=cls._tokenizer.eos_token_id,
                    eos_token_id=cls._tokenizer.eos_token_id,
                    repetition_penalty=1.2
                )

            # Decodificar apenas a resposta gerada (sem o prompt)
            response = cls._tokenizer.decode(
                outputs[0][inputs['input_ids'].shape[1]:],
                skip_special_tokens=True
            )

            # Limpar resposta
            response = cls._clean_response(response)

            # Limpar memória após geração
            del inputs, outputs
            gc.collect()

            return response

        except Exception as e:
            print(f"❌ [Phi-3] Erro ao gerar resposta: {e}")
            import traceback
            traceback.print_exc()

            # Tentar limpar memória mesmo em caso de erro
            gc.collect()
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
        Constrói prompt otimizado para Phi-3
        Phi-3 usa formato específico: <|system|>, <|user|>, <|assistant|>
        """

        if context is None:
            context = {}

        is_greeting = context.get("is_greeting", False)

        # ⭐ SISTEMA DIRETO E CLARO (Phi-3 entende contexto complexo)
        system_prompt = """Você é um consultor especialista em vendas de delivery de comida.

SEU OBJETIVO:
1. Entender o que o cliente quer
2. Perguntar para quantas pessoas
3. Sugerir produtos complementares

REGRAS:
• Use APENAS os produtos listados
• Quando cliente diz apenas categoria (pizza, hambúrguer), pergunte qual tipo/sabor
• SEMPRE pergunte "para quantas pessoas?"
• Sugira bebida e sobremesa quando apropriado
• Máximo 50 palavras
• NÃO fale sobre você mesmo

EXEMPLOS DE RESPOSTAS CORRETAS:

Cliente: "pizza"
→ "Temos Pizza Margherita (R$ 35) e Calabresa (R$ 38). Qual prefere? Para quantas pessoas?"

Cliente: "quero margherita"
→ "Pizza Margherita - R$ 35 (serve 2 pessoas). Quantas quer? Adiciono uma bebida?"

Cliente: "estou entre pizza e mexicana"
→ "Pizza serve 2+ pessoas (R$ 35-40), burrito é individual (R$ 28). Quantas pessoas são?"

Cliente: "oi"
→ "Olá! O que você gostaria de pedir?"

RESPONDA AGORA:"""

        # Produtos (formato limpo e direto)
        products_context = ""
        if has_results and products:
            products_context = "\n\nPRODUTOS DISPONÍVEIS:\n"
            for p in products[:5]:
                parts = [f"R$ {p['price']:.2f}"]
                if p.get('serves_people'):
                    parts.append(f"serve {p['serves_people']}p")
                if p.get('preparation_time_minutes'):
                    parts.append(f"{p['preparation_time_minutes']}min")
                products_context += f"• {p['name']} - {', '.join(parts)}\n"
        elif not is_greeting:
            products_context = "\n\nNenhum produto encontrado."

        # Carrinho
        cart_context = ""
        if cart:
            items = [f"{item.get('name', 'item')} x{item.get('quantity', 1)}" for item in cart]
            total = sum([item.get('price', 0) * item.get('quantity', 1) for item in cart])
            cart_context = f"\n\nCARRINHO: {', '.join(items)} | Total: R$ {total:.2f}"

        # ⭐ FORMATO ESPECÍFICO DO PHI-3
        prompt = f"""<|system|>
{system_prompt}<|end|>
<|user|>
{products_context}{cart_context}

Cliente disse: "{user_message}"

Responda em português, máximo 50 palavras, seguindo os exemplos acima.<|end|>
<|assistant|>
"""

        return prompt

    @classmethod
    def _clean_response(cls, response: str) -> str:
        """
        Limpa e valida resposta do Phi-3
        Phi-3 é muito melhor, mas ainda precisa de limpeza básica
        """
        response = response.strip()

        # Remover tags específicas do Phi-3
        for tag in ['<|end|>', '<|system|>', '<|user|>', '<|assistant|>', '<|endoftext|>']:
            response = response.replace(tag, '')

        # Parar em quebra de linha dupla
        if '\n\n' in response:
            response = response.split('\n\n')[0].strip()

        # Remover múltiplos espaços
        response = ' '.join(response.split())

        # Limitar comprimento (segurança)
        if len(response) > 250:
            sentences = response.split('.')
            if len(sentences) > 2:
                response = '. '.join(sentences[:2]) + '.'
            else:
                response = response[:247] + '...'

        # Validar se muito curto
        if len(response) < 10:
            print(f"⚠️  [Phi-3] Resposta muito curta: '{response}'")
            return "Não entendi. Pode reformular? O que você gostaria de pedir?"

        # Remover caracteres especiais no início
        response = response.lstrip('.-:;,!? ')

        # Garantir pontuação no final
        if response and response[-1] not in '.!?':
            if response[-1].isalnum():
                response += '.'

        return response.strip()

    @classmethod
    def is_ready(cls) -> bool:
        """Verifica se o modelo está carregado e pronto"""
        return cls._is_initialized and cls._model is not None

