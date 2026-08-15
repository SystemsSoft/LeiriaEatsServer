"""
Hybrid AI Service
Integra busca semântica (E5) com conversação natural (Google Gemini 1.5 Flash)
"""
from services.ai_service import AIService
from services.gemini_sales_service import GeminiSalesAgent
from services.session_service import SessionManager, UserSession
from sqlalchemy.orm import Session
from typing import Dict, List, Optional


class HybridAIService:
    """
    Pipeline híbrido que combina:
    1. E5 (Busca semântica local) - Entende intenção e busca produtos
    2. Google Gemini 1.5 Flash (Conversação API) - Transforma resultados em diálogo natural
    Age como um vendedor conversacional real, inteligente e consultivo
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

    # Palavras que indicam confirmação/finalização do pedido
    _ORDER_CONFIRMATION = {
        "sim", "confirma", "confirmar", "confirmado", "pode mandar", "pode fechar",
        "fecha o pedido", "finaliza", "finalizar", "finalizado", "quero confirmar",
        "pode confirmar", "tá bom", "ta bom", "ok", "isso mesmo", "perfeito",
        "pode ir", "manda", "quero esse", "quero esses", "aceito", "fechado",
        "pode fazer", "faz o pedido", "faz", "vai", "bora", "pode"
    }

    @staticmethod
    def _detect_order_confirmation(message: str, session) -> bool:
        """
        Detecta se o usuário está confirmando o pedido.
        Retorna True se a mensagem indicar confirmação, independente do carrinho.
        """
        msg_lower = message.lower().strip()
        return any(word in msg_lower for word in HybridAIService._ORDER_CONFIRMATION)

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
        db: Session,
        session_id: Optional[str] = None
    ) -> Dict:
        """
        Pipeline GENERATIVO completo - E5 busca + Gemini conversa
        Usa SessionManager para manter contexto entre mensagens
        """

        print(f"💬 [Chat] Mensagem recebida: '{user_message}'")

        # ── SESSÃO ────────────────────────────────────────────────────────
        session = SessionManager.get_or_create(session_id, restaurant_id)
        
        # Sincronizar restaurant_id caso tenha sido omitido na request mas já exista na sessão
        if not restaurant_id and session.restaurant_id:
            restaurant_id = session.restaurant_id
            
        print(f"👤 [Session] ID: {session.session_id[:8]}... | Restaurante: {restaurant_id} | Carrinho: {len(session.cart)} item(s) | Histórico: {len(session.history)} msg(s)")

        # Adicionar mensagem do usuário ao histórico
        session.add_message("user", user_message)

        # FASE 1: Detectar tipo de intenção e necessidades
        intent_info = HybridAIService._detect_intent_type(user_message)
        intent_type = intent_info["type"]
        print(f"🎯 [Intent] Tipo detectado: {intent_type}")
        if intent_info.get("details"):
            print(f"   Detalhes: {intent_info['details']}")

        # FASE 2: Buscar produtos SEMPRE (mesmo em consultas/saudações)
        # Isso dá contexto completo ao Gemini
        print(f"🔍 [E5] Buscando produtos relevantes...")

        # Buscar com E5 (semantic search)
        search_results = AIService.process_search(
            user_query=user_message,
            db=db,
            scope="product"
        )

        # ⭐ ESTRATÉGIA HÍBRIDA DE PRODUTOS:
        # 1. Se tem restaurant_id: pega TODOS os produtos do restaurante do banco
        #    e usa o E5 apenas para ordenar por relevância
        # 2. Se não tem restaurant_id: usa os top 6 do E5

        if restaurant_id:
            from core.sql_models import ProductDB as ProductDBModel
            # Buscar todos os produtos do restaurante diretamente do banco
            db_products = db.query(ProductDBModel).filter(
                ProductDBModel.restaurant_id == restaurant_id
            ).all()

            # Mapear IDs dos resultados E5 para ter os scores de relevância
            e5_scores = {}
            for p in search_results.productResults:
                e5_scores[p.id] = True  # marcador de relevância E5

            # Converter produtos do banco para objetos do cache do AIService
            all_products = []
            e5_relevant = []  # produtos que o E5 considerou relevantes
            other_products = []  # demais produtos do restaurante

            for db_prod in db_products:
                # Encontrar o objeto Product no cache do AIService
                cached = next((p for p in AIService._product_obj_cache if p.id == db_prod.id), None)
                if cached:
                    if db_prod.id in e5_scores:
                        e5_relevant.append(cached)
                    else:
                        other_products.append(cached)

            # Priorizar produtos relevantes pelo E5, depois os demais do restaurante
            all_products = e5_relevant + other_products
        else:
            all_products = search_results.productResults

        found_products = []
        seen_ids = set()
        for product in all_products[:8]:
            if product.id in seen_ids:
                continue
            seen_ids.add(product.id)

            def _get(attr, default=None):
                return getattr(product, attr, default)

            product_data = {
                "id": product.id,
                "name": product.name,
                "price": float(product.price),
                "restaurant_id": _get("restaurant_id"),
                "image_url": _get("image_url"),
                "description": _get("description", ""),
                "category": _get("category", ""),
                "rating": _get("rating"),
                "is_available": _get("is_available", True),
                "is_popular": _get("is_popular", False),
                # Porção e pessoas
                "serves_people": _get("serves_people"),
                "portion_size": _get("portion_size"),
                # Tempo de preparo
                "preparation_time_minutes": _get("preparation_time_minutes"),
                "preparation_time": _get("preparation_time"),
                # Ingredientes e composição
                "ingredients": _get("ingredients"),
                "allergens": _get("allergens"),
                "dietary_tags": _get("dietary_tags"),
                "spice_level": _get("spice_level"),
                # Nutrição
                "calories": _get("calories"),
                # Contexto de uso
                "recommended_for": _get("recommended_for"),
                "search_tags": _get("search_tags"),
            }
            found_products.append(product_data)

        print(f"✅ [E5] Encontrou {len(found_products)} produtos")

        # FASE 3: Gemini SEMPRE responde (generativo completo via API)
        print(f"🤖 [Gemini] Gerando resposta conversacional...")

        # ── DETECÇÃO DE CONFIRMAÇÃO DE PEDIDO ─────────────────────────────
        order_confirmed = HybridAIService._detect_order_confirmation(user_message, session)
        if order_confirmed:
            print(f"🎉 [Session] Pedido confirmado pelo usuário!")

        # Preparar contexto COMPLETO para Gemini (produtos + sessão + histórico)
        context = {
            "products": found_products,
            "cart": session.get_cart_as_list(),
            "user_query": user_message,
            "has_results": len(found_products) > 0,
            "history_text": session.get_history_text(),
            "session_context": session.context,
            "order_confirmed": order_confirmed,

            # ⭐ Passar informações de intent para guiar o modelo
            "intent_type": intent_type,
            "is_greeting": intent_type == "greeting",
            "consultation_mode": intent_type in ["consultation_needed", "general_question"],
            "specific_question": intent_type == "specific_question",
            "user_needs": intent_info.get("details", {})
        }

        # ⭐ USAR GEMINI SEMPRE (API cloud - sem peso no servidor)
        try:
            if GeminiSalesAgent.is_ready():
                ai_response = GeminiSalesAgent.generate_response(
                    user_message=user_message,
                    context=context
                )
                print(f"✅ [Gemini] Resposta gerada!")
                used_ai = True
            else:
                print("⚠️  [Gemini] Não disponível, usando fallback")
                ai_response = HybridAIService._generate_fallback_response(
                    found_products,
                    user_message,
                    session.get_cart_as_list()
                )
                used_ai = False
        except Exception as e:
            print(f"❌ [Gemini] Erro ao gerar resposta: {e}")
            import traceback
            traceback.print_exc()
            print("⚠️  Usando fallback por segurança")
            ai_response = HybridAIService._generate_fallback_response(
                found_products,
                user_message,
                session.get_cart_as_list()
            )
            used_ai = False

        # Filtrar: retornar apenas produtos que a IA mencionou na resposta
        mentioned_products = HybridAIService._filter_mentioned_products(ai_response, found_products)
        print(f"📦 [Products] {len(mentioned_products)}/{len(found_products)} produtos mencionados na resposta")

        # Salvar resposta da IA no histórico da sessão
        session.add_message("assistant", ai_response)

        print(f"✅ Resposta final gerada")

        return {
            "response": ai_response,
            "products": mentioned_products,
            "intent": intent_type,
            "semantic_search_used": True,
            "ai_generated": used_ai,
            "needs_mapped": intent_info["details"],
            "session_id": session.session_id,
            "cart": session.get_cart_summary(),
            "order_confirmed": order_confirmed,
            "restaurant_id": session.restaurant_id,
        }

    @staticmethod
    def _filter_mentioned_products(ai_response: str, products: List[Dict]) -> List[Dict]:
        """
        Retorna apenas os produtos cujo nome aparece na resposta do Gemini.
        Usa comparação case-insensitive e ignora acentos para maior precisão.
        """
        import unicodedata

        def normalize(text: str) -> str:
            """Remove acentos e converte para minúsculas"""
            return ''.join(
                c for c in unicodedata.normalize('NFD', text.lower())
                if unicodedata.category(c) != 'Mn'
            )

        response_normalized = normalize(ai_response)
        mentioned = []

        for product in products:
            name = product.get("name", "")
            if not name:
                continue
            name_normalized = normalize(name)
            # Verifica se o nome completo OU pelo menos 2 palavras significativas aparecem
            words = [w for w in name_normalized.split() if len(w) > 3]
            full_match = name_normalized in response_normalized
            partial_match = len(words) >= 2 and sum(
                1 for w in words if w in response_normalized
            ) >= min(2, len(words))

            if full_match or partial_match:
                mentioned.append(product)

        return mentioned

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
            return (
                "Para te ajudar com as quantidades, me diga:\n"
                "• O que você quer pedir?\n"
                "• Quantas pessoas vão comer?\n"
                "• É para almoço, jantar ou lanche?\n\n"
                "Assim eu sugiro as quantidades ideais! 😊"
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
        Resposta simples caso Gemini não esteja disponível
        Age como vendedor conversacional, usa informações inteligentes dos produtos
        """
        if not products:
            # Sem produtos encontrados - oferecer ajuda
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
            
            return f"Encontrei: {p['name']}{desc}{extra_text} por R$ {p['price']:.2f}. Quantos você quer?"

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
        Inicializa ambos os modelos (E5 local e Gemini API)
        Deve ser chamado na inicialização do servidor
        """
        print("🚀 Inicializando modelos de IA...")

        e5_loaded = False
        gemini_loaded = False

        # Carregar E5 (busca semântica local)
        try:
            print("📡 Carregando E5 (Sentence Transformer)...")
            AIService.get_model()
            print("✅ E5 carregado!")
            e5_loaded = True
        except Exception as e:
            print(f"❌ Erro ao carregar E5: {e}")
            print("⚠️  Sistema continuará sem busca semântica")

        # Configurar Gemini (API cloud - instantâneo)
        try:
            print("🤖 Configurando Gemini API...")
            GeminiSalesAgent.initialize()
            print("✅ Gemini configurado!")
            gemini_loaded = True
        except Exception as e:
            print(f"❌ Erro ao configurar Gemini: {e}")
            print("⚠️  Sistema usará respostas de fallback")

        # Resumo
        if e5_loaded and gemini_loaded:
            print("🎉 Todos os modelos foram carregados com sucesso!")
            print("💡 Sistema híbrido: E5 (busca local) + Gemini (conversação cloud)")
        elif e5_loaded:
            print("⚠️  Sistema parcialmente operacional (apenas E5)")
        elif gemini_loaded:
            print("⚠️  Sistema parcialmente operacional (apenas Gemini)")
        else:
            print("❌ Nenhum modelo foi carregado - sistema em modo degradado")

    @staticmethod
    def get_status() -> Dict:
        """Retorna status dos modelos e uso da API"""
        gemini_status = GeminiSalesAgent.get_usage_status() if GeminiSalesAgent.is_ready() else None
        cache_status = GeminiSalesAgent.get_cache_status() if GeminiSalesAgent.is_ready() else None

        return {
            "e5_loaded": AIService._model is not None,
            "gemini_ready": GeminiSalesAgent.is_ready(),
            "gemini_usage": gemini_status,
            "gemini_cache": cache_status,
            "system_ready": (
                AIService._model is not None and
                GeminiSalesAgent.is_ready()
            )
        }
