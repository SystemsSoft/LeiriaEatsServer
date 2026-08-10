"""
Hybrid AI Service
Integra busca semântica (E5) com conversação natural (Phi-3-Mini)
"""
from services.ai_service import AIService
from services.phi3_sales_service import Phi3SalesAgent
from sqlalchemy.orm import Session
from typing import Dict, List, Optional


class HybridAIService:
    """
    Pipeline híbrido que combina:
    1. E5 (Busca semântica) - Entende intenção e busca produtos
    2. Phi-3-Mini (Conversação) - Transforma resultados em diálogo natural
    Age como um vendedor conversacional real
    """

    # Palavras de saudação
    _GREETINGS = {
        "oi", "olá", "ola", "hey", "opa", "e aí", "eai", "iae",
        "bom dia", "boa tarde", "boa noite", "alô", "alo",
        "hello", "hi", "hola", "salve", "fala"
    }

    # Perguntas gerais (sem menção a comida)
    _GENERAL_QUESTIONS = {
        "como vai", "tudo bem", "como está", "como tu tá",
        "beleza", "suave", "tranquilo", "legal", "como vc está"
    }

    # Palavras de dúvida/indecisão
    _DOUBT_INDICATORS = {
        "dúvida", "duvida", "não sei", "nao sei", "indeciso", "indecisa",
        "entre", "ou", "qual", "qual escolher", "me ajuda a escolher",
        "não tenho certeza", "nao tenho certeza", "o que você sugere",
        "o que vc sugere", "me sugere", "recomenda", "recomendar"
    }

    # Palavras de recomendação
    _RECOMMENDATION_REQUESTS = {
        "sugere", "sugestão", "sugestao", "recomenda", "recomendação", "recomendacao",
        "o que tem", "o que você tem", "o que vc tem", "opções", "opcoes",
        "cardápio", "cardapio", "menu", "o que é bom", "o que e bom"
    }

    # Palavras sobre quantidade
    _QUANTITY_INDICATORS = {
        "quantos", "quantas", "quantidade", "tamanho", "porção", "porcao",
        "serve quantas pessoas", "para quantas pessoas",
        "individual", "família", "familia"
    }
    
    # Tamanhos de produtos (não devem ser tratados como perguntas)
    _PRODUCT_SIZES = {"grande", "média", "medio", "pequeno", "pequena", "gg", "m", "p"}

    # Palavras sobre bebidas
    _DRINK_INDICATORS = {
        "bebida", "refrigerante", "suco", "água", "agua", "cerveja",
        "vinho", "drink", "algo para beber", "pra beber"
    }

    # Palavras sobre sobremesas
    _DESSERT_INDICATORS = {
        "sobremesa", "doce", "pudim", "sorvete", "bolo", "torta",
        "mousse", "brigadeiro", "açaí", "acai", "brownie"
    }

    @staticmethod
    def _detect_intent_type(message: str) -> Dict:
        """
        Detecta o tipo de intenção do usuário de forma mais inteligente

        Returns:
            Dicionário com:
                - type: tipo principal da intenção
                - details: detalhes específicos detectados
                - needs_consultation: se precisa de conversa consultiva
        """
        msg_lower = message.lower().strip()

        # Saudações
        if msg_lower in HybridAIService._GREETINGS:
            return {
                "type": "greeting",
                "details": {},
                "needs_consultation": False
            }

        # Perguntas gerais
        if any(q in msg_lower for q in HybridAIService._GENERAL_QUESTIONS):
            return {
                "type": "general_question",
                "details": {},
                "needs_consultation": False
            }

        # Detectar múltiplas necessidades
        details = {
            "has_doubt": any(d in msg_lower for d in HybridAIService._DOUBT_INDICATORS),
            "wants_recommendation": any(r in msg_lower for r in HybridAIService._RECOMMENDATION_REQUESTS),
            "asks_quantity": any(q in msg_lower for q in HybridAIService._QUANTITY_INDICATORS),
            "mentions_drink": any(d in msg_lower for d in HybridAIService._DRINK_INDICATORS),
            "mentions_dessert": any(d in msg_lower for d in HybridAIService._DESSERT_INDICATORS),
        }
        
        # Detectar se é pedido específico de produto (ex: "quero pizza grande")
        is_specific_order = any(word in msg_lower for word in ["quero", "pedir", "adicionar", "me traz", "vou querer"])
        has_product_size = any(size in msg_lower for size in HybridAIService._PRODUCT_SIZES)
        
        # Se é pedido específico com tamanho, não é pergunta sobre quantidade
        if is_specific_order and has_product_size:
            details["asks_quantity"] = False

        # Detectar se está em dúvida entre opções (ex: "entre pizza e mexicana")
        has_comparison = " ou " in msg_lower or " entre " in msg_lower

        # Se está em dúvida ou quer recomendação, precisa de consulta
        if details["has_doubt"] or details["wants_recommendation"] or has_comparison:
            return {
                "type": "consultation_needed",
                "details": details,
                "needs_consultation": True,
                "comparison": has_comparison
            }

        # Se pergunta sobre quantidade, bebida ou sobremesa
        if details["asks_quantity"] or details["mentions_drink"] or details["mentions_dessert"]:
            return {
                "type": "specific_question",
                "details": details,
                "needs_consultation": True
            }

        # Default: busca de produto específica
        return {
            "type": "product_search",
            "details": details,
            "needs_consultation": False
        }

    @staticmethod
    def process_sales_chat(
        user_message: str,
        restaurant_id: Optional[int],
        cart: List[Dict],
        db: Session
    ) -> Dict:
        """
        Pipeline GENERATIVO completo - E5 busca + Phi-3-Mini conversa

        MUDANÇA IMPORTANTE: Agora usa Phi-3-Mini para TODAS as respostas
        Sem templates estáticos - IA decide baseada em contexto completo

        Args:
            user_message: Mensagem do usuário
            restaurant_id: ID do restaurante (opcional, para filtrar busca)
            cart: Lista de itens no carrinho atual
            db: Sessão do banco de dados

        Returns:
            Dicionário com:
                - response: Resposta conversacional do Phi-3-Mini
                - products: Lista de produtos encontrados pelo E5
                - intent: Intenção detectada
                - semantic_search_used: True/False
        """

        print(f"💬 [Chat] Mensagem recebida: '{user_message}'")

        # FASE 1: Detectar tipo de intenção e necessidades
        intent_info = HybridAIService._detect_intent_type(user_message)
        intent_type = intent_info["type"]
        print(f"🎯 [Intent] Tipo detectado: {intent_type}")
        if intent_info.get("details"):
            print(f"   Detalhes: {intent_info['details']}")

        # FASE 2: Buscar produtos SEMPRE (mesmo em consultas/saudações)
        # Isso dá contexto completo ao Phi-3-Mini
        print(f"🔍 [E5] Buscando produtos relevantes...")

        search_query = user_message
        if restaurant_id:
            search_query = f"{user_message} restaurant:{restaurant_id}"

        # Buscar com E5 (semantic search)
        search_results = AIService.process_search(
            user_query=search_query,
            db=db,
            scope="product"
        )

        # Extrair produtos encontrados com TODAS as informações
        found_products = []
        for product in search_results.productResults[:5]:  # Top 5 produtos
            product_data = {
                "id": product.id,
                "name": product.name,
                "price": float(product.price),
                "description": product.description if hasattr(product, 'description') else "",
                "category": product.category if hasattr(product, 'category') else "",
                "quantity": product.quantity if hasattr(product, 'quantity') else 1,
                # Informações inteligentes para IA
                "ingredients": product.ingredients if hasattr(product, 'ingredients') else None,
                "allergens": product.allergens if hasattr(product, 'allergens') else None,
                "dietary_tags": product.dietary_tags if hasattr(product, 'dietary_tags') else None,
                "spice_level": product.spice_level if hasattr(product, 'spice_level') else None,
                "serves_people": product.serves_people if hasattr(product, 'serves_people') else None,
                "portion_size": product.portion_size if hasattr(product, 'portion_size') else None,
                "calories": product.calories if hasattr(product, 'calories') else None,
                "is_popular": product.is_popular if hasattr(product, 'is_popular') else False,
                "preparation_time_minutes": product.preparation_time_minutes if hasattr(product, 'preparation_time_minutes') else None,
                "recommended_for": product.recommended_for if hasattr(product, 'recommended_for') else None,
                "search_tags": product.search_tags if hasattr(product, 'search_tags') else None,
            }
            found_products.append(product_data)

        print(f"✅ [E5] Encontrou {len(found_products)} produtos")

        # FASE 3: Phi-3-Mini SEMPRE responde (generativo completo)
        print(f"🤖 [Phi-3-Mini] Gerando resposta conversacional generativa...")

        # Preparar contexto COMPLETO para Phi-3-Mini
        context = {
            "products": found_products,
            "cart": cart,
            "user_query": user_message,
            "has_results": len(found_products) > 0,

            # ⭐ Passar informações de intent para guiar o modelo
            "intent_type": intent_type,
            "is_greeting": intent_type == "greeting",
            "consultation_mode": intent_type in ["consultation_needed", "general_question"],
            "specific_question": intent_type == "specific_question",
            "user_needs": intent_info.get("details", {})
        }

        # ⭐ USAR PHI-3-MINI SEMPRE (não mais fallback)
        try:
            if Phi3SalesAgent.is_ready():
                ai_response = Phi3SalesAgent.generate_response(
                    user_message=user_message,
                    context=context
                )
                print(f"✅ [Phi-3-Mini] Resposta generativa criada!")
                used_ai = True
            else:
                print("⚠️  [Phi-3-Mini] Não disponível, usando fallback")
                ai_response = HybridAIService._generate_fallback_response(
                    found_products,
                    user_message,
                    cart
                )
                used_ai = False
        except Exception as e:
            print(f"❌ [Phi-3-Mini] Erro ao gerar resposta: {e}")
            import traceback
            traceback.print_exc()
            print("⚠️  Usando fallback por segurança")
            ai_response = HybridAIService._generate_fallback_response(
                found_products,
                user_message,
                cart
            )
            used_ai = False

        print(f"✅ Resposta final gerada")

        return {
            "response": ai_response,
            "products": found_products,
            "intent": intent_type,
            "semantic_search_used": True,
            "ai_generated": used_ai,
            "needs_mapped": intent_info["details"]
        }

    @staticmethod
    def _generate_consultation_response(message: str, intent_info: Dict, cart: List[Dict]) -> str:
        """
        Gera resposta consultiva quando o usuário está em dúvida
        """
        msg_lower = message.lower()
        details = intent_info.get("details", {})
        
        # Detectar preferências alimentares específicas
        dietary_preferences = []
        if any(word in msg_lower for word in ["vegetariano", "vegetariana"]):
            dietary_preferences.append("vegetariano")
        if any(word in msg_lower for word in ["vegano", "vegana", "vegan"]):
            dietary_preferences.append("vegano")
        if any(word in msg_lower for word in ["sem glúten", "sem gluten", "celíaco", "celiaco"]):
            dietary_preferences.append("sem glúten")
        if any(word in msg_lower for word in ["sem lactose", "intolerante a lactose"]):
            dietary_preferences.append("sem lactose")
        
        # Se mencionou preferências dietéticas específicas
        if dietary_preferences:
            prefs = " e ".join(dietary_preferences)
            return (
                f"Perfeito! Vou te mostrar opções {prefs}! 🌱\n\n"
                f"Me conta mais:\n"
                f"• Você prefere algo leve ou uma refeição completa?\n"
                f"• Tem algum ingrediente que você evita?\n"
                f"• É para quantas pessoas?\n\n"
                f"Assim consigo te sugerir as melhores opções!"
            )
        
        # Detectar se mencionou tipos de comida
        food_types_mentioned = []
        if "pizza" in msg_lower:
            food_types_mentioned.append("pizza")
        if "mexicana" in msg_lower or "mexicano" in msg_lower or "taco" in msg_lower or "burrito" in msg_lower:
            food_types_mentioned.append("comida mexicana")
        if "hamburguer" in msg_lower or "hambúrguer" in msg_lower or "burger" in msg_lower:
            food_types_mentioned.append("hambúrguer")
        if "japonesa" in msg_lower or "japonês" in msg_lower or "sushi" in msg_lower:
            food_types_mentioned.append("comida japonesa")
        if "italiana" in msg_lower or "italiano" in msg_lower or "massa" in msg_lower:
            food_types_mentioned.append("comida italiana")
        
        # Se mencionou 2+ tipos de comida, dar orientação
        if len(food_types_mentioned) >= 2:
            tipos = " e ".join(food_types_mentioned)
            return (
                f"Entendo sua dúvida entre {tipos}! 🤔 Para te ajudar melhor, me conta:\n\n"
                f"• Você está com fome de algo leve ou mais pesado?\n"
                f"• Prefere algo para comer com as mãos ou de garfo e faca?\n"
                f"• Quanto tempo tem para comer? (entrega rápida ou pode esperar um pouco?)\n\n"
                f"Assim consigo te dar uma sugestão perfeita! 😊"
            )
        
        # Se quer recomendação geral
        if details.get("wants_recommendation"):
            return (
                "Claro! Temos várias opções deliciosas! 😋\n\n"
                "Me diga o que você está procurando:\n"
                "• Algo rápido como lanches e pizzas?\n"
                "• Uma refeição completa?\n"
                "• Vegetariano/vegano?\n"
                "• Alguma preferência de cozinha (italiana, mexicana, japonesa...)?\n\n"
                "Ou me fale o que você está com vontade e eu te ajudo!"
            )
        
        # Resposta genérica consultiva
        return (
            "Fico feliz em te ajudar a escolher! 😊\n\n"
            "Para te dar a melhor sugestão, me conta:\n"
            "• O que você está com vontade de comer?\n"
            "• Algo específico que você goste ou evite?\n"
            "• Quantas pessoas vão comer?\n\n"
            "Assim monto o pedido perfeito para você!"
        )

    @staticmethod
    def _generate_specific_answer(message: str, intent_info: Dict, cart: List[Dict]) -> str:
        """
        Responde perguntas específicas sobre quantidade, bebidas, sobremesas
        """
        details = intent_info.get("details", {})
        
        if details.get("asks_quantity"):
            if cart:
                return (
                    "Sobre as quantidades, depende do que você quer! 😊\n\n"
                    "Me diga:\n"
                    "• Quantas pessoas vão comer?\n"
                    "• É para almoço, jantar ou lanche?\n"
                    "• Vocês comem bastante ou moderado?\n\n"
                    "Assim eu sugiro as quantidades ideais!"
                )
            else:
                return (
                    "Para te ajudar com as quantidades, preciso saber:\n"
                    "• O que você quer pedir?\n"
                    "• Quantas pessoas vão comer?\n\n"
                    "Me fala o que você tem em mente! 😊"
                )
        
        if details.get("mentions_drink"):
            return (
                "Temos várias opções de bebidas! 🥤\n\n"
                "Me conta:\n"
                "• Prefere refrigerante, suco natural ou água?\n"
                "• Com gás ou sem gás?\n"
                "• Algum sabor específico?\n\n"
                "Posso te mostrar o que temos disponível!"
            )
        
        if details.get("mentions_dessert"):
            return (
                "Sobremesas são sempre uma ótima pedida! 🍰\n\n"
                "O que você prefere:\n"
                "• Algo gelado (sorvete, açaí)?\n"
                "• Sobremesa tradicional (pudim, mousse)?\n"
                "• Chocolate ou frutas?\n\n"
                "Me fala sua preferência que eu te mostro as opções!"
            )
        
        # Resposta padrão
        return (
            "Claro, posso te ajudar com isso! 😊\n"
            "Me dá mais detalhes sobre o que você precisa que eu te oriento melhor."
        )

    @staticmethod
    def _generate_fallback_response(products: List[Dict], user_message: str, cart: List[Dict]) -> str:
        """
        Resposta simples caso Phi-3-Mini não esteja disponível
        Age como vendedor conversacional, usa informações inteligentes dos produtos
        """
        if not products:
            # Sem produtos encontrados - oferecer ajuda
            if cart:
                cart_items = ", ".join([item.get('name', 'item') for item in cart])
                return f"Você já tem {cart_items} no carrinho. Não encontrei '{user_message}' no cardápio. Pode descrever melhor ou escolher outra coisa?"
            else:
                return "Hmm, não encontrei isso no nosso cardápio. 🤔 Pode me dizer de outra forma? Por exemplo: 'pizza', 'hambúrguer', 'refrigerante'..."

        if len(products) == 1:
            # Um produto - descrever com detalhes inteligentes
            p = products[0]
            desc = f" - {p['description']}" if p.get('description') else ""
            
            # Adicionar informações úteis
            extra_info = []
            if p.get('serves_people'):
                extra_info.append(f"serve {p['serves_people']} pessoa{'s' if p['serves_people'] > 1 else ''}")
            if p.get('preparation_time_minutes'):
                extra_info.append(f"pronto em {p['preparation_time_minutes']} min")
            if p.get('dietary_tags'):
                tags = p['dietary_tags'].split(',')[0].strip()  # Primeira tag
                extra_info.append(tags)
            
            extra_text = f" ({', '.join(extra_info)})" if extra_info else ""
            
            return f"Encontrei: {p['name']}{desc}{extra_text} por R$ {p['price']:.2f}. Quantos você quer adicionar?"

        # Múltiplos produtos - listar com informações inteligentes
        products_list = []
        for p in products[:3]:
            # Adicionar tags relevantes
            tags = []
            if p.get('is_popular'):
                tags.append("⭐ Popular")
            if p.get('dietary_tags'):
                diet_tag = p['dietary_tags'].split(',')[0].strip()
                tags.append(f"🌱 {diet_tag}")
            if p.get('spice_level') and 'picante' in p['spice_level'].lower() and p['spice_level'] != 'não picante':
                tags.append("🌶️ Picante")
            if p.get('preparation_time_minutes') and p['preparation_time_minutes'] < 20:
                tags.append("⚡ Rápido")
            
            tag_text = f" {' '.join(tags)}" if tags else ""
            products_list.append(f"• {p['name']} - R$ {p['price']:.2f}{tag_text}")
        
        return f"Tenho estas opções:\n" + "\n".join(products_list) + "\n\nQual te interessa?"

    @staticmethod
    def initialize_models():
        """
        Inicializa ambos os modelos (E5 e Phi-3-Mini)
        Deve ser chamado na inicialização do servidor
        """
        print("🚀 Inicializando modelos de IA...")

        e5_loaded = False
        phi3_loaded = False

        # Carregar E5 (já existente)
        try:
            print("📡 Carregando E5 (Sentence Transformer)...")
            AIService.get_model()
            print("✅ E5 carregado!")
            e5_loaded = True
        except Exception as e:
            print(f"❌ Erro ao carregar E5: {e}")
            print("⚠️  Sistema continuará sem E5")

        # Carregar Phi-3-Mini (novo)
        try:
            print("🤖 Carregando Phi-3-Mini...")
            Phi3SalesAgent.initialize()
            print("✅ Phi-3-Mini carregado!")
            phi3_loaded = True
        except Exception as e:
            print(f"❌ Erro ao carregar Phi-3-Mini: {e}")
            print("⚠️  Sistema usará respostas de fallback")

        # Resumo
        if e5_loaded and phi3_loaded:
            print("🎉 Todos os modelos foram carregados com sucesso!")
        elif e5_loaded:
            print("⚠️  Sistema parcialmente operacional (apenas E5)")
        elif phi3_loaded:
            print("⚠️  Sistema parcialmente operacional (apenas Phi-3-Mini)")
        else:
            print("❌ Nenhum modelo foi carregado - sistema em modo degradado")

    @staticmethod
    def get_status() -> Dict:
        """Retorna status dos modelos"""
        return {
            "e5_loaded": AIService._model is not None,
            "phi3_loaded": Phi3SalesAgent.is_ready(),
            "system_ready": (
                AIService._model is not None and
                Phi3SalesAgent.is_ready()
            )
        }
