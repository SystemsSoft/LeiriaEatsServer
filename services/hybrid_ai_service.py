"""
Hybrid AI Service
Integra busca semântica (E5) com conversação natural (Google Gemini 1.5 Flash)
"""
from services.ai_service import AIService
from services.gemini_sales_service import GeminiSalesAgent
from services.session_service import SessionManager, UserSession
from repositories.restaurant_repo import RestaurantRepository
from core.config import settings
from sqlalchemy.orm import Session
from typing import Dict, List, Optional
import json
import os


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

    # F3 do PLANO_EXECUCAO_IA.md — atrás de flag, caminho de tags mantido como padrão.
    # Ligar só depois de validar o round-trip contra a API viva (ver nota em
    # GeminiSalesAgent._TOOLS — bloqueado nesta sessão por créditos esgotados da API key).
    MAX_QTD_ITEM_FUNCTION_CALLING = 20

    # PLANO_LIMITE_RESTAURANTES.md — mesmo valor lido pelo gate de checkout
    # (core/config.py), para os dois lados nunca divergirem.
    MAX_RESTAURANTES_POR_PEDIDO = settings.MAX_RESTAURANTES_POR_PEDIDO

    @staticmethod
    def _function_calling_habilitado() -> bool:
        return os.getenv("IA_FUNCTION_CALLING", "false").strip().lower() == "true"

    @staticmethod
    def _filtrar_pool_por_restaurantes_travados(candidate_pool: list, session: UserSession) -> list:
        """
        PLANO_LIMITE_RESTAURANTES.md, Fase 3.1 — quando o limite de restaurantes já foi
        atingido, o pool de produtos oferecido à IA passa a conter só os restaurantes já
        escolhidos. Sem isso a IA continuaria "vendo" produtos de outros restaurantes e
        tentando sugeri-los, mesmo sabendo que não pode adicioná-los (a regra do prompt
        cobriria isso, mas é mais robusto a IA nem enxergar a opção que não pode oferecer).

        Itens que já estão no carrinho continuam SEMPRE visíveis, mesmo que por alguma
        inconsistência de dados seu restaurant_gid divirja dos "travados" — sem essa
        cláusula, o cliente ficaria preso com um item que a conversa não consegue mais
        remover (o pool é a única fonte de produtos que a IA recebe por turno).
        """
        restaurantes_travados = session.restaurantes_no_carrinho()
        if len(restaurantes_travados) < HybridAIService.MAX_RESTAURANTES_POR_PEDIDO:
            return candidate_pool

        ids_no_carrinho = {item.product_id for item in session.cart}
        return [
            p for p in candidate_pool
            if p.id in ids_no_carrinho or getattr(p, "restaurant_gid", "") in restaurantes_travados
        ]

    @staticmethod
    def _filtrar_pool_por_aptidao_de_pagamento(candidate_pool: list, session: UserSession) -> list:
        """
        Fase 0 do PLANO_PAGAMENTO_2_ETAPAS.md — a IA nunca oferece produto de restaurante
        sem conta Stripe apta a receber pagamento. Bloquear só no checkout (que já existe
        e continua existindo como rede de segurança) faz o cliente descobrir o problema só
        depois de montar o pedido inteiro pela conversa; filtrando aqui, ele nunca chega a
        essa situação — o 400 do checkout vira caso raro (a situação do restaurante mudou
        ENTRE a conversa e o pagamento), não o caminho normal.

        Cache vazio (índice ainda não carregou) não filtra nada — falha para o lado
        permissivo, porque o checkout continua protegendo mesmo se este filtro não agir.
        Itens já no carrinho continuam sempre visíveis, pela mesma razão do filtro de
        limite de restaurantes: se a situação do restaurante mudar no meio da conversa, o
        cliente precisa conseguir remover o item pela própria conversa.
        """
        aptos = AIService._restaurantes_aptos_pagamento
        if not aptos:
            return candidate_pool

        ids_no_carrinho = {item.product_id for item in session.cart}
        return [
            p for p in candidate_pool
            if p.id in ids_no_carrinho or getattr(p, "restaurant_gid", "") in aptos
        ]

    @staticmethod
    def _bloqueado_por_limite_restaurantes(session: UserSession, gid_restaurante_produto: str,
                                            delta: int) -> bool:
        """
        PLANO_LIMITE_RESTAURANTES.md, Fase 2 — regra única compartilhada pelo executor de
        function calling e pelos dois laços de parsing de tags (stream e síncrono), para
        as três camadas nunca divergirem sobre o que conta como "restaurante novo".

        Só bloqueia ADIÇÃO (delta > 0) de um restaurante que ainda não está no carrinho,
        e só quando o limite já foi atingido. Remoção (delta <= 0) e itens de um
        restaurante já presente no carrinho nunca são bloqueados — é assim que o limite
        libera sozinho quando o cliente desiste de um restaurante.
        """
        if delta <= 0 or not gid_restaurante_produto:
            return False
        restaurantes_atuais = session.restaurantes_no_carrinho()
        e_restaurante_novo = gid_restaurante_produto not in restaurantes_atuais
        return e_restaurante_novo and len(restaurantes_atuais) >= HybridAIService.MAX_RESTAURANTES_POR_PEDIDO

    @staticmethod
    def _executar_ferramenta(nome_ferramenta: str, args: Dict, session: UserSession,
                              found_products: List[Dict], estado_turno: Dict) -> Dict:
        """
        F3.2 do plano — executor validado das ferramentas do Gemini. Só aqui a ação é
        de fato aplicada; o resultado devolvido volta ao modelo antes da resposta final,
        o que impede a IA de afirmar uma ação que não foi executada.

        `estado_turno` é um dict mutável usado para sinalizar efeitos que não são
        "resposta da ferramenta" no sentido estrito (ex.: mostrar a sacola) sem precisar
        de variáveis globais ou nonlocal.
        """
        if nome_ferramenta == "adicionar_ao_carrinho":
            gid = args.get("product_gid")
            try:
                delta = int(args.get("delta_quantidade", 0))
            except (TypeError, ValueError):
                return {"ok": False, "erro": "QUANTIDADE_INVALIDA"}

            produto = next((p for p in found_products if p["gid"] == gid), None)
            if produto is None:
                return {"ok": False, "erro": "GID_FORA_DO_CATALOGO",
                        "dica": "Use apenas um GID exato listado em PRODUTOS DISPONÍVEIS."}
            if not produto.get("is_available", True):
                return {"ok": False, "erro": "PRODUTO_INDISPONIVEL", "produto": produto["name"]}

            # PLANO_LIMITE_RESTAURANTES.md, Fase 2.1 — ver _bloqueado_por_limite_restaurantes.
            if HybridAIService._bloqueado_por_limite_restaurantes(
                session, produto.get("restaurant_gid") or "", delta
            ):
                return {
                    "ok": False,
                    "erro": "LIMITE_DE_RESTAURANTES_ATINGIDO",
                    "limite": HybridAIService.MAX_RESTAURANTES_POR_PEDIDO,
                    "restaurantes_atuais": list(session.nomes_restaurantes_no_carrinho().values()),
                    "dica": ("Explique o limite ao cliente e ofereça remover os itens de um dos "
                             "restaurantes atuais para abrir espaço."),
                }

            atual = next((i.quantity for i in session.cart if i.product_id == produto["id"]), 0)
            alvo = max(0, min(atual + delta, HybridAIService.MAX_QTD_ITEM_FUNCTION_CALLING))
            if alvo == atual:
                return {"ok": False, "erro": "SEM_EFEITO", "quantidade_atual": atual}

            session.add_to_cart(
                product_id=produto["id"], name=produto["name"], price=produto["price"],
                restaurant_gid=produto["restaurant_gid"], quantity=alvo - atual,
                serves_people=produto.get("serves_people") or 1, category=produto.get("category", ""),
                restaurant_name=produto.get("restaurant_name", ""),
            )
            estado_turno["carrinho_mudou"] = True
            return {"ok": True, "produto": produto["name"], "quantidade_final": alvo}

        if nome_ferramenta == "mostrar_sacola":
            estado_turno["show_cart"] = True
            return {"ok": True}

        if nome_ferramenta == "sugerir_produtos":
            # F4.2: substitui a heurística de casar nome de produto no texto da resposta
            # (_filter_mentioned_products) — o modelo declara os GIDs explicitamente.
            gids_pedidos = args.get("gids") or []
            pool_por_gid = {p["gid"]: p for p in found_products}
            gids_validos = [g for g in gids_pedidos if g in pool_por_gid]
            estado_turno["gids_sugeridos"] = gids_validos
            if len(gids_validos) < len(gids_pedidos):
                return {"ok": True, "aviso": "ALGUNS_GIDS_FORA_DO_CATALOGO_FORAM_IGNORADOS",
                        "gids_aceitos": gids_validos}
            return {"ok": True, "gids_aceitos": gids_validos}

        return {"ok": False, "erro": "FERRAMENTA_DESCONHECIDA"}

    # ── F4.1 do PLANO_EXECUCAO_IA.md — session.context preenchido de verdade ───────
    #
    # O plano original propunha extrair pessoas/categoria_atual/aguardando via
    # structured output do Gemini (uma chamada extra por turno). Não implementamos
    # dessa forma agora: a GEMINI_API_KEY do projeto está com créditos esgotados (ver
    # ANALISE_IA_PEDIDO.md/relato desta sessão), então (a) não dava para validar contra
    # a API viva, e (b) dobrar o número de chamadas por turno é a última coisa que faz
    # sentido durante um incidente de cota. Em vez disso, extraímos os mesmos três
    # campos por heurística em Python — sem custo de API, testável offline — e deixamos
    # documentado que migrar para structured output é o caminho natural de evolução.
    _NUMEROS_POR_EXTENSO = {
        "um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3,
        "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8,
    }
    _PADRAO_PESSOAS = None  # compilado sob demanda (evita custo de import re no module load)

    @classmethod
    def _extrair_pessoas(cls, mensagem: str) -> Optional[int]:
        """Só captura número de pessoas quando a frase menciona 'pessoa(s)' ou 'somos' —
        não qualquer dígito solto (que na maioria das vezes é quantidade de produto,
        ex.: 'quero 2 pizzas' não deve virar 'pedido para 2 pessoas')."""
        import re
        if cls._PADRAO_PESSOAS is None:
            numeros = "|".join(list(cls._NUMEROS_POR_EXTENSO.keys()) + [r"\d+"])
            cls._PADRAO_PESSOAS = re.compile(
                rf"(?:somos|seremos|para)\s+({numeros})\s*(?:pessoas?)?\b"
                rf"|({numeros})\s+pessoas?\b",
                re.IGNORECASE,
            )
        m = cls._PADRAO_PESSOAS.search(mensagem.lower())
        if not m:
            return None
        texto_numero = m.group(1) or m.group(2)
        if texto_numero.isdigit():
            return int(texto_numero)
        return cls._NUMEROS_POR_EXTENSO.get(texto_numero)

    @staticmethod
    def _extrair_categoria_dominante(found_products: List[Dict]) -> Optional[str]:
        """Se a busca deste turno convergiu para uma única categoria, é um bom sinal de
        'o cliente está a escolher X' para o próximo turno — sem categoria dominante
        (produtos de categorias variadas, ou pool vazio), não define nada."""
        categorias = [p.get("category") for p in found_products if p.get("category")]
        if not categorias:
            return None
        unicas = set(categorias)
        if len(unicas) == 1:
            return next(iter(unicas))
        return None

    @staticmethod
    def _extrair_aguardando(ai_response_limpa: str, intent_info: Dict) -> Optional[str]:
        """Se a resposta da IA termina em pergunta, guarda uma pista curta do que se
        espera do cliente na próxima mensagem — usado pelo prompt para não repetir a
        mesma pergunta (ex.: perguntar quantidade duas vezes)."""
        if not ai_response_limpa.rstrip().endswith("?"):
            return None
        details = intent_info.get("details", {})
        if details.get("asks_quantity"):
            return "quantidade"
        if intent_info.get("type") == "consultation_needed":
            return "escolha entre opções"
        return "confirmação do pedido"

    @classmethod
    def _atualizar_contexto_sessao(cls, session: UserSession, user_message: str,
                                    ai_response_limpa: str, intent_info: Dict,
                                    found_products: List[Dict]) -> None:
        """Atualiza session.context in-place. Só sobrescreve um campo quando há um sinal
        novo — não apaga um valor já conhecido (ex.: 'pessoas' definido numa mensagem
        anterior) só porque a mensagem atual não repetiu a informação."""
        pessoas = cls._extrair_pessoas(user_message)
        if pessoas:
            session.context["pessoas"] = pessoas

        categoria = cls._extrair_categoria_dominante(found_products)
        if categoria:
            session.context["categoria_atual"] = categoria

        session.context["aguardando"] = cls._extrair_aguardando(ai_response_limpa, intent_info)

    @staticmethod
    def process_sales_chat_stream(
        user_message: str,
        restaurant_gid: Optional[str],
        db: Session,
        session_id: Optional[str] = None
    ):
        """Versão em STREAMING do pipeline híbrido"""
        print(f"🌊 [Chat Stream] Mensagem recebida: '{user_message}'")

        import time as _time_module  # F0: cronômetro do turno inteiro (telemetria)
        from services import telemetry
        turn_start = _time_module.time()

        # 1. Preparação idêntica à síncrona
        session = SessionManager.get_or_create(session_id, restaurant_gid)
        if not restaurant_gid and session.restaurant_gid:
            restaurant_gid = session.restaurant_gid

        restaurant_id = None
        if restaurant_gid:
            res_db = RestaurantRepository.get_by_gid(db, restaurant_gid)
            if res_db:
                restaurant_id = res_db.id

        session.add_message("user", user_message)
        intent_info = HybridAIService._detect_intent_type(user_message)
        intent_type = intent_info["type"]

        # 2. Pool de produtos (Consistente com a versão síncrona)
        import time
        from services.ai_service import AIService
        from core.sql_models import ProductDB as ProductDBModel
        from sqlalchemy.orm import joinedload

        # F2.1: com restaurante fixo e menu pequeno, TODOS os produtos vão para o pool
        # de qualquer forma (linha abaixo, e5_relevant + other_products) e a ordenação
        # do E5 é desfeita logo depois pela prioridade carrinho → sugestões → resto.
        # Rodar o encode do E5 nesse caso paga latência por um resultado descartado.
        POOL_CUTOFF_STREAM = 20
        usar_e5 = True
        if restaurant_id:
            n_produtos_restaurante = db.query(ProductDBModel).filter(
                ProductDBModel.restaurant_id == restaurant_id
            ).count()
            usar_e5 = n_produtos_restaurante > POOL_CUTOFF_STREAM

        start_time = time.time()
        if usar_e5:
            search_results = AIService.process_search(user_query=user_message, db=db, scope="product")
        else:
            from schemas.models import SearchResponse
            search_results = SearchResponse(reply="", intent="skip_e5_menu_pequeno", restaurantResults=[], productResults=[])
        ms_e5 = (time.time() - start_time) * 1000
        print(f"⏱️  [E5 Search] Tempo: {ms_e5:.1f}ms (usado={usar_e5})")

        start_pool = time.time()
        if restaurant_id:
            db_products = db.query(ProductDBModel).options(joinedload(ProductDBModel.restaurant)).filter(
                ProductDBModel.restaurant_id == restaurant_id
            ).all()

            seen_ids_local = set()
            e5_ids = {p.id for p in search_results.productResults}

            e5_relevant = []
            other_products = []
            for db_prod in db_products:
                seen_ids_local.add(db_prod.id)
                cached = AIService._product_by_id.get(db_prod.id)
                if cached:
                    if db_prod.id in e5_ids: e5_relevant.append(cached)
                    else: other_products.append(cached)

            all_products = e5_relevant + other_products
            for gp in search_results.productResults:
                if gp.id not in seen_ids_local: all_products.append(gp)
        else:
            all_products = AIService._product_obj_cache if len(AIService._product_obj_cache) <= 50 else search_results.productResults

        candidate_pool = []
        seen_ids = set()
        # Itens do carrinho e sugestões anteriores têm prioridade no contexto
        for item in session.cart:
            if item.product_id not in seen_ids:
                p = AIService._product_by_id.get(item.product_id)
                if p: candidate_pool.append(p); seen_ids.add(p.id)

        for pid in getattr(session, 'last_suggested_ids', []):
            if pid not in seen_ids:
                p = AIService._product_by_id.get(pid)
                if p: candidate_pool.append(p); seen_ids.add(p.id)

        for product in all_products:
            if product.id not in seen_ids:
                candidate_pool.append(product); seen_ids.add(product.id)

        # PLANO_LIMITE_RESTAURANTES.md, Fase 3.1
        candidate_pool = HybridAIService._filtrar_pool_por_restaurantes_travados(candidate_pool, session)
        # PLANO_PAGAMENTO_2_ETAPAS.md, Fase 0
        candidate_pool = HybridAIService._filtrar_pool_por_aptidao_de_pagamento(candidate_pool, session)

        found_products = []
        produtos_sem_gid_excluidos = 0
        for product in candidate_pool[:20]: # Aumentado para 20 para segurança
            gid = getattr(product, "gid", "") or ""
            if not gid:
                # Sem GID, o Gemini não tem como referenciar o produto na tag
                # [[ADD_TO_CART:GID:QTD]] — oferecê-lo só produz uma promessa que não
                # pode ser cumprida. Ver F1.4 do PLANO_EXECUCAO_IA.md.
                produtos_sem_gid_excluidos += 1
                continue
            p_data = {
                "id": product.id,
                "gid": gid,
                "name": product.name,
                "price": float(product.price),
                "restaurant_gid": getattr(product, "restaurant_gid", "") or restaurant_gid or "",
                # Ver PLANO_LIMITE_RESTAURANTES.md Fase 2.3 — usado pela IA e pelo
                # executor para se referir ao restaurante pelo nome, não pelo GID.
                "restaurant_name": AIService._restaurant_name_by_product_id.get(product.id, ""),
                "image_url": getattr(product, "image_url", ""),
                "description": getattr(product, "description", ""),
                "category": getattr(product, "category", ""),
                "rating": getattr(product, "rating", None),
                "is_popular": getattr(product, "is_popular", False),
                "is_available": getattr(product, "is_available", True),
                "serves_people": getattr(product, "serves_people", 1),
                "quantity": 0 # Valor base
            }
            found_products.append(p_data)
        if produtos_sem_gid_excluidos:
            print(f"⚠️ [Pool] {produtos_sem_gid_excluidos} produto(s) sem GID excluído(s) do pool")

        # Injetar quantidades reais do carrinho no found_products
        cart_map = {item.product_id: item.quantity for item in session.cart}
        for p in found_products:
            if p["id"] in cart_map:
                p["quantity"] = cart_map[p["id"]]

        context = {
            "products": found_products,
            "cart": session.get_cart_as_list(),
            "history_text": session.get_history_text(),
            "session_context": session.context,
            "intent_type": intent_type,
            "user_needs": intent_info.get("details", {}),
            # PLANO_LIMITE_RESTAURANTES.md, Fase 3.2
            "restaurantes_no_pedido": session.nomes_restaurantes_no_carrinho(),
            "max_restaurantes_por_pedido": HybridAIService.MAX_RESTAURANTES_POR_PEDIDO,
        }
        ms_pool = (time.time() - start_pool) * 1000
        print(f"⏱️  [Context Prep] Tempo: {ms_pool:.1f}ms")

        # 3. Iniciar Stream com Filtro de Tags
        full_ai_response = ""
        tag_buffer = ""
        is_inside_tag = False

        start_gen = time.time()
        ms_ttft = None
        print(f"🤖 [Gemini] Iniciando geração de conteúdo...")
        try:
            chunk_count = 0
            for text_chunk in GeminiSalesAgent.generate_response_stream(user_message, context):
                if chunk_count == 0:
                    ms_ttft = (time.time() - start_gen) * 1000
                    print(f"⏱️  [Gemini TTFT] Tempo para o primeiro chunk: {ms_ttft:.1f}ms")
                chunk_count += 1
                full_ai_response += text_chunk

                # Lógica de processamento caractere a caractere para filtrar tags [[...]]
                for char in text_chunk:
                    if not is_inside_tag:
                        if char == '[':
                            tag_buffer += '['
                            if tag_buffer == '[[':
                                is_inside_tag = True
                        else:
                            # Se tínhamos um '[' isolado e veio outro caractere, libera o '['
                            if tag_buffer:
                                yield {"type": "chunk", "text": tag_buffer}
                                tag_buffer = ""
                            yield {"type": "chunk", "text": char}
                    else:
                        tag_buffer += char
                        if tag_buffer.endswith(']]'):
                            is_inside_tag = False
                            tag_buffer = "" # Descarta a tag completa

            if tag_buffer and not is_inside_tag:
                yield {"type": "chunk", "text": tag_buffer}

            print(f"⏱️  [Gemini Stream] Tempo total de geração: {time.time() - start_gen:.4f}s")

            # 3. Pós-processamento interno (tags, carrinho, mostrar sacola)
            import re
            add_to_cart_matches = re.findall(r"\[\[ADD_TO_CART:([A-Z0-9]+):(-?\d+)\]\]", full_ai_response)

            # Se houve qualquer alteração no carrinho (adição ou remoção), ativamos o show_cart
            has_cart_action = len(add_to_cart_matches) > 0
            show_cart = "[[SHOW_CART]]" in full_ai_response or has_cart_action

            # F0: fidelidade — quantas tags o modelo emitiu vs. quantas foram de fato
            # aplicadas ao carrinho, e por qual motivo as demais foram descartadas.
            tags_aplicadas = 0
            tags_descartadas_motivo = []
            for prod_gid, qty_str in add_to_cart_matches:
                qty = int(qty_str)
                product_info = next((p for p in found_products if p["gid"] == prod_gid), None)
                if product_info is None:
                    tags_descartadas_motivo.append("GID_FORA_DO_POOL")
                    print(f"⚠️ [Gemini Stream] Produto GID {prod_gid} não encontrado no pool de contexto!")
                elif HybridAIService._bloqueado_por_limite_restaurantes(
                    session, product_info.get("restaurant_gid") or "", qty
                ):
                    # PLANO_LIMITE_RESTAURANTES.md, Fase 2.2 — mesma regra do executor de
                    # function calling, aplicada aqui porque o caminho de tags é o que
                    # roda em produção por padrão (IA_FUNCTION_CALLING desligada).
                    tags_descartadas_motivo.append("LIMITE_DE_RESTAURANTES")
                    print(f"⚠️ [Gemini Stream] Limite de {HybridAIService.MAX_RESTAURANTES_POR_PEDIDO} "
                          f"restaurantes atingido — GID {prod_gid} descartado.")
                else:
                    session.add_to_cart(
                        product_id=product_info["id"], name=product_info["name"],
                        price=product_info["price"], restaurant_gid=product_info["restaurant_gid"],
                        quantity=qty, serves_people=product_info.get("serves_people") or 1,
                        restaurant_name=product_info.get("restaurant_name", ""),
                    )
                    tags_aplicadas += 1

            # Limpar tags da resposta para o histórico
            clean_response = full_ai_response.replace("[[SHOW_CART]]", "")
            for m in add_to_cart_matches:
                clean_response = clean_response.replace(f"[[ADD_TO_CART:{m[0]}:{m[1]}]]", "")
            clean_response = ' '.join(clean_response.split()).strip()

            # 🛠️ CAPTURA DE DADOS FINAL (Sincronizada com o carrinho atualizado)
            final_cart_summary = session.get_cart_summary()
            new_cart_map = {item["product_id"]: item["quantity"] for item in final_cart_summary["items"]}

            # 1. Obter Sugestões (Mencionados no texto)
            # Nota: products_pool contém até 15-20 itens relevantes para a conversa
            suggested_products = HybridAIService._filter_mentioned_products(clean_response, found_products)

            # 2. Obter Detalhes do Carrinho (cartProducts)
            cart_products = []
            cart_prod_ids = {item["product_id"] for item in final_cart_summary["items"]}

            for cp_id in cart_prod_ids:
                # Tentar achar no pool de busca primeiro (mais eficiente)
                p_from_pool = next((p for p in found_products if p["id"] == cp_id), None)
                if p_from_pool:
                    cart_products.append(p_from_pool.copy())
                else:
                    # Se não estava no pool (ex: item de conversa antiga), buscar no cache global
                    full_p = AIService._product_by_id.get(cp_id)
                    if full_p:
                        cart_products.append({
                            "id": full_p.id, "gid": getattr(full_p, "gid", ""), "name": full_p.name,
                            "price": float(full_p.price), "restaurant_gid": getattr(full_p, "restaurant_gid", "") or restaurant_gid or "",
                            "image_url": getattr(full_p, "image_url", ""), "description": getattr(full_p, "description", ""),
                            "category": getattr(full_p, "category", ""), "rating": getattr(full_p, "rating", None),
                            "is_popular": getattr(full_p, "is_popular", False), "is_available": getattr(full_p, "is_available", True),
                            "serves_people": getattr(full_p, "serves_people", 1)
                        })

            # 3. ATUALIZAR QUANTIDADES em ambas as listas
            for p in suggested_products:
                p["quantity"] = new_cart_map.get(p["id"], 0)
            for p in cart_products:
                p["quantity"] = new_cart_map.get(p["id"], 0)

            # 4. BUSCAR RESTAURANTES (De todos os produtos envolvidos)
            mentioned_restaurants = []
            seen_res_gids = set()
            all_involved_products = suggested_products + cart_products

            for p in all_involved_products:
                res_gid = p.get("restaurant_gid")
                if res_gid and res_gid not in seen_res_gids:
                    res_db = RestaurantRepository.get_by_gid(db, res_gid)
                    if res_db:
                        mentioned_restaurants.append({
                            "id": res_db.id, "gid": res_db.gid, "name": res_db.name,
                            "category": res_db.category, "rating": res_db.rating,
                            "image_url": res_db.image_url, "latitude": res_db.latitude,
                            "longitude": res_db.longitude
                        })
                        seen_res_gids.add(res_gid)

            session.add_message("assistant", clean_response)
            session.last_suggested_ids = [p["id"] for p in suggested_products]

            # F4.1: atualiza pessoas/categoria_atual/aguardando para o próximo turno
            HybridAIService._atualizar_contexto_sessao(
                session, user_message, clean_response, intent_info, found_products
            )

            # Persistir sessão
            SessionManager.save(session)

            # Enviar metadados finais
            print(f"📦 [Stream] Enviando {len(suggested_products)} sugestões e {len(cart_products)} itens no carrinho.")

            telemetry.registrar_turno(
                session_id=session.session_id,
                restaurant_gid=restaurant_gid,
                modelo=GeminiSalesAgent._escolher_modelo(context),
                ms_e5=ms_e5,
                ms_pool=ms_pool,
                ms_ttft=ms_ttft,
                ms_total=(time.time() - turn_start) * 1000,
                pool_size=len(found_products),
                tokens_prompt_estimado=GeminiSalesAgent._ultimo_tamanho_prompt // 4,
                tags_emitidas=len(add_to_cart_matches),
                tags_aplicadas=tags_aplicadas,
                tags_descartadas_motivo=tags_descartadas_motivo,
                promessa_de_acao=telemetry.texto_promete_acao_carrinho(clean_response),
                divergencia_de_preco=telemetry.detectar_divergencia_de_preco(clean_response, found_products),
                intent_type=intent_type,
                origem="stream",
                restaurantes_no_carrinho=len(session.restaurantes_no_carrinho()),
                limite_restaurantes_atingido=len(session.restaurantes_no_carrinho()) >= HybridAIService.MAX_RESTAURANTES_POR_PEDIDO,
            )

            yield {
                "type": "final",
                "session_id": session.session_id,
                "cart": final_cart_summary,
                "products": suggested_products,
                "cartProducts": cart_products,
                "restaurantResults": mentioned_restaurants,
                "show_cart": show_cart,
                "order_confirmed": False
            }

        except Exception as e:
            print(f"❌ [Stream Error]: {e}")
            import traceback
            traceback.print_exc()
            try:
                telemetry.registrar_turno(
                    session_id=session.session_id,
                    restaurant_gid=restaurant_gid,
                    modelo=GeminiSalesAgent._escolher_modelo(context) if 'context' in locals() else "desconhecido",
                    ms_e5=ms_e5 if 'ms_e5' in locals() else None,
                    ms_pool=ms_pool if 'ms_pool' in locals() else None,
                    ms_ttft=ms_ttft if 'ms_ttft' in locals() else None,
                    ms_total=(time.time() - turn_start) * 1000,
                    pool_size=len(found_products) if 'found_products' in locals() else 0,
                    tokens_prompt_estimado=0,
                    tags_emitidas=0,
                    tags_aplicadas=0,
                    motivo_fallback=f"exception:{type(e).__name__}",
                    intent_type=intent_type,
                    origem="stream",
                    restaurantes_no_carrinho=len(session.restaurantes_no_carrinho()),
                )
            except Exception:
                pass  # telemetria nunca deve mascarar o erro original
            yield {"type": "error", "message": str(e)}

            # 🛡️ PROTEÇÃO DE DADOS: Mesmo em erro, tenta enviar o estado atual do carrinho
            try:
                yield {
                    "type": "final",
                    "session_id": session.session_id,
                    "cart": session.get_cart_summary(),
                    "products": [],
                    "order_confirmed": False
                }
            except: pass

    @staticmethod
    def process_sales_chat(
        user_message: str,
        restaurant_gid: Optional[str],
        cart: List[Dict],
        db: Session,
        session_id: Optional[str] = None
    ) -> Dict:
        """
        Pipeline GENERATIVO completo - E5 busca + Gemini conversa
        Usa SessionManager para manter contexto entre mensagens
        """
        import time as _time_module
        from services import telemetry
        turn_start = _time_module.time()

        print(f"💬 [Chat] Mensagem recebida: '{user_message}'")

        # ── SESSÃO ────────────────────────────────────────────────────────
        session = SessionManager.get_or_create(session_id, restaurant_gid)

        # Sincronizar restaurant_gid caso tenha sido omitido na request mas já exista na sessão
        if not restaurant_gid and session.restaurant_gid:
            restaurant_gid = session.restaurant_gid

        print(f"👤 [Session] ID: {session.session_id[:8]}... | Restaurante GID: {restaurant_gid} | Carrinho: {len(session.cart)} item(s) | Histórico: {len(session.history)} msg(s)")

        # Converter GID para ID interno para consultas SQL
        restaurant_id = None
        if restaurant_gid:
            res_db = RestaurantRepository.get_by_gid(db, restaurant_gid)
            if res_db:
                restaurant_id = res_db.id

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
        from core.sql_models import ProductDB as ProductDBModel
        from sqlalchemy.orm import joinedload

        # F2.1: com restaurante fixo e menu pequeno, TODOS os produtos vão para o pool
        # de qualquer forma (all_products = e5_relevant + other_products, abaixo) —
        # rodar o E5 nesse caso paga latência por um resultado descartado.
        POOL_CUTOFF_SYNC = 15
        usar_e5 = True
        if restaurant_id:
            n_produtos_restaurante = db.query(ProductDBModel).filter(
                ProductDBModel.restaurant_id == restaurant_id
            ).count()
            usar_e5 = n_produtos_restaurante > POOL_CUTOFF_SYNC

        print(f"🔍 [E5] Buscando produtos relevantes... (usado={usar_e5})")

        _e5_start = _time_module.time()
        if usar_e5:
            search_results = AIService.process_search(
                user_query=user_message,
                db=db,
                scope="product"
            )
        else:
            from schemas.models import SearchResponse
            search_results = SearchResponse(reply="", intent="skip_e5_menu_pequeno", restaurantResults=[], productResults=[])
        ms_e5 = (_time_module.time() - _e5_start) * 1000

        # ⭐ ESTRATÉGIA HÍBRIDA DE PRODUTOS:
        # 1. Se tem restaurant_id: pega TODOS os produtos do restaurante do banco
        #    e usa o E5 apenas para ordenar por relevância
        # 2. Se não tem restaurant_id: usa os top 6 do E5

        if restaurant_id:
            # Buscar todos os produtos do restaurante diretamente do banco com o GID carregado
            db_products = db.query(ProductDBModel).options(joinedload(ProductDBModel.restaurant)).filter(
                ProductDBModel.restaurant_id == restaurant_id
            ).all()

            # Mapear IDs dos resultados E5 para ter os scores de relevância
            e5_scores = {}
            seen_ids_local = set()
            for p in search_results.productResults:
                e5_scores[p.id] = True  # marcador de relevância E5

            # Converter produtos do banco para objetos do cache do AIService
            all_products = []
            e5_relevant = []  # produtos que o E5 considerou relevantes
            other_products = []  # demais produtos do restaurante

            for db_prod in db_products:
                seen_ids_local.add(db_prod.id)
                # Encontrar o objeto Product no cache do AIService
                cached = AIService._product_by_id.get(db_prod.id)
                if cached:
                    if db_prod.id in e5_scores:
                        e5_relevant.append(cached)
                    else:
                        other_products.append(cached)

            # Priorizar produtos relevantes pelo E5, depois os demais do restaurante
            all_products = e5_relevant + other_products

            # Adicionar também os resultados globais do E5 que não são deste restaurante
            for global_prod in search_results.productResults:
                if global_prod.id not in seen_ids_local:
                    all_products.append(global_prod)
        else:
            # ⭐ MELHORIA: Em busca global, se o número de produtos total for pequeno,
            # enviamos todos para a IA ter contexto completo.
            if len(AIService._product_obj_cache) <= 50:
                all_products = AIService._product_obj_cache
            else:
                all_products = search_results.productResults

        # ⭐ NOVO: Unir resultados da busca com itens que já estão no carrinho
        # E também com produtos sugeridos na última interação (Memória de Sugestões)
        candidate_pool = []
        seen_ids = set()

        # 1. Prioridade para itens do carrinho
        for item in session.cart:
            if item.product_id not in seen_ids:
                full_product = AIService._product_by_id.get(item.product_id)
                if full_product:
                    candidate_pool.append(full_product)
                    seen_ids.add(item.product_id)

        # 2. Prioridade para produtos sugeridos na mensagem anterior
        last_suggested = getattr(session, 'last_suggested_ids', [])
        for prod_id in last_suggested:
            if prod_id not in seen_ids:
                full_product = AIService._product_by_id.get(prod_id)
                if full_product:
                    candidate_pool.append(full_product)
                    seen_ids.add(prod_id)

        # 3. Adicionar resultados da busca atual
        for product in all_products:
            if product.id not in seen_ids:
                candidate_pool.append(product)
                seen_ids.add(product.id)

        # PLANO_LIMITE_RESTAURANTES.md, Fase 3.1
        candidate_pool = HybridAIService._filtrar_pool_por_restaurantes_travados(candidate_pool, session)
        # PLANO_PAGAMENTO_2_ETAPAS.md, Fase 0
        candidate_pool = HybridAIService._filtrar_pool_por_aptidao_de_pagamento(candidate_pool, session)

        found_products = []
        produtos_sem_gid_excluidos = 0
        # Pegamos até 15 produtos para dar um contexto rico (carrinho + busca)
        for product in candidate_pool[:15]:

            def _get(attr, default=None):
                return getattr(product, attr, default)

            if not _get("gid", ""):
                # Sem GID, o Gemini não consegue referenciar o produto na tag
                # [[ADD_TO_CART:GID:QTD]] — ver F1.4 do PLANO_EXECUCAO_IA.md.
                produtos_sem_gid_excluidos += 1
                continue

            product_data = {
                "id": product.id, # ID interno para o servidor
                "gid": _get("gid", ""), # GID para a IA e Frontend
                "name": product.name,
                "price": float(product.price),
                "restaurant_gid": (getattr(product, "restaurant_gid", "") or restaurant_gid) or "", # Garantir que nunca retorne null
                # Ver PLANO_LIMITE_RESTAURANTES.md Fase 2.3.
                "restaurant_name": AIService._restaurant_name_by_product_id.get(product.id, ""),
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

        if produtos_sem_gid_excluidos:
            print(f"⚠️ [Pool] {produtos_sem_gid_excluidos} produto(s) sem GID excluído(s) do pool")
        print(f"✅ [E5] Encontrou {len(found_products)} produtos")

        # FASE 3: Gemini SEMPRE responde (generativo completo via API)
        print(f"🤖 [Gemini] Gerando resposta conversacional...")

        # Preparar contexto COMPLETO para Gemini (produtos + sessão + histórico)
        context = {
            "products": found_products,
            "cart": session.get_cart_as_list(),
            "user_query": user_message,
            "has_results": len(found_products) > 0,
            "history_text": session.get_history_text(),
            "session_context": session.context,
            "order_confirmed": False, # IA agora decide a confirmação

            # ⭐ Passar informações de intent para guiar o modelo
            "intent_type": intent_type,
            "is_greeting": intent_type == "greeting",
            "consultation_mode": intent_type in ["consultation_needed", "general_question"],
            "specific_question": intent_type == "specific_question",
            "user_needs": intent_info.get("details", {}),
            # PLANO_LIMITE_RESTAURANTES.md, Fase 3.2
            "restaurantes_no_pedido": session.nomes_restaurantes_no_carrinho(),
            "max_restaurantes_por_pedido": HybridAIService.MAX_RESTAURANTES_POR_PEDIDO,
        }

        # ⭐ USAR GEMINI SEMPRE (API cloud - sem peso no servidor)
        order_confirmed = False
        add_to_cart_matches = []
        tags_aplicadas = 0
        tags_descartadas_motivo = []
        motivo_fallback = None
        gids_sugeridos_fc = None  # F4.2: só definido quando o caminho de function calling roda
        usar_function_calling = HybridAIService._function_calling_habilitado()
        try:
            if GeminiSalesAgent.is_ready() and usar_function_calling:
                # F3: caminho novo, atrás de flag — ver nota de validação pendente em
                # GeminiSalesAgent._TOOLS. Mantido isolado do caminho de tags abaixo para
                # que uma reversão seja só a variável de ambiente, sem deploy de código.
                estado_turno = {"carrinho_mudou": False, "show_cart": False}

                def _executor(nome_ferramenta, args):
                    return HybridAIService._executar_ferramenta(
                        nome_ferramenta, args, session, found_products, estado_turno
                    )

                resultado = GeminiSalesAgent.generate_response_with_tools(
                    user_message=user_message, context=context, executor=_executor
                )
                ai_response = resultado["text"]
                acoes = resultado["acoes_executadas"]
                add_to_cart_matches = [a for a in acoes if a["ferramenta"] == "adicionar_ao_carrinho"]
                tags_aplicadas = sum(1 for a in add_to_cart_matches if a["resultado"].get("ok"))
                tags_descartadas_motivo = [
                    a["resultado"].get("erro", "ERRO_DESCONHECIDO")
                    for a in add_to_cart_matches if not a["resultado"].get("ok")
                ]
                show_cart = estado_turno["show_cart"] or estado_turno["carrinho_mudou"]
                gids_sugeridos_fc = estado_turno.get("gids_sugeridos", [])
                print(f"✅ [Gemini FunctionCalling] {len(acoes)} ação(ões) executada(s), resposta: {ai_response}")
                used_ai = True
            elif GeminiSalesAgent.is_ready():
                ai_response = GeminiSalesAgent.generate_response(
                    user_message=user_message,
                    context=context
                )

                print(f"🤖 [Gemini] Resposta bruta: {ai_response}")

                # 🔍 1. DETECÇÃO DE ADIÇÃO/REMOÇÃO NO CARRINHO VIA TAG DO GEMINI
                import re
                add_to_cart_matches = re.findall(r"\[\[ADD_TO_CART:([A-Z0-9]+):(-?\d+)\]\]", ai_response)

                # Se houve qualquer alteração no carrinho, ativamos o show_cart
                has_cart_action = len(add_to_cart_matches) > 0

                # F0: fidelidade — tags emitidas vs. aplicadas (ver process_sales_chat_stream)
                tags_aplicadas = 0
                tags_descartadas_motivo = []
                for prod_gid, qty_str in add_to_cart_matches:
                    qty = int(qty_str)

                    # Buscar detalhes do produto no pool encontrado via GID
                    product_info = next((p for p in found_products if p["gid"] == prod_gid), None)
                    if product_info is None:
                        print(f"⚠️ [Gemini] Produto GID {prod_gid} não encontrado no pool de contexto!")
                        tags_descartadas_motivo.append("GID_FORA_DO_POOL")
                    elif HybridAIService._bloqueado_por_limite_restaurantes(
                        session, product_info.get("restaurant_gid") or "", qty
                    ):
                        # PLANO_LIMITE_RESTAURANTES.md, Fase 2.2
                        print(f"⚠️ [Gemini] Limite de {HybridAIService.MAX_RESTAURANTES_POR_PEDIDO} "
                              f"restaurantes atingido — GID {prod_gid} descartado.")
                        tags_descartadas_motivo.append("LIMITE_DE_RESTAURANTES")
                    else:
                        print(f"🛒 [Gemini] Adicionando ao carrinho: {product_info['name']} x{qty}")
                        session.add_to_cart(
                            product_id=product_info["id"], # Mantemos o ID interno na sessão para performance
                            name=product_info["name"],
                            price=product_info["price"],
                            restaurant_gid=product_info["restaurant_gid"],
                            quantity=qty,
                            serves_people=product_info.get("serves_people") or 1,
                            category=product_info.get("category", ""),
                            restaurant_name=product_info.get("restaurant_name", ""),
                        )
                        tags_aplicadas += 1

                    # Limpar a tag da resposta
                    ai_response = ai_response.replace(f"[[ADD_TO_CART:{prod_gid}:{qty_str}]]", "")

                # 🔍 2. DETECÇÃO DE MOSTRAR SACOLA VIA TAG DO GEMINI
                show_cart = "[[SHOW_CART]]" in ai_response or has_cart_action

                # 🔍 3. LIMPEZA GLOBAL DE TAGS DA RESPOSTA (Segurança)
                # Remove qualquer coisa entre [[ ]] para o usuário não ver
                ai_response = re.sub(r"\[\[.*?\]\]", "", ai_response)

                # Limpeza final de espaços duplos resultantes da remoção de tags
                ai_response = ' '.join(ai_response.split()).strip()

                print(f"✅ [Gemini] Resposta final processada: {ai_response}")

                print(f"✅ [Gemini] Resposta gerada!")
                used_ai = True
            else:
                print("⚠️  [Gemini] Não disponível, usando fallback")
                motivo_fallback = "gemini_nao_pronto"
                ai_response = HybridAIService._generate_fallback_response(
                    found_products,
                    user_message,
                    session.get_cart_as_list()
                )
                show_cart = False
                used_ai = False
        except Exception as e:
            print(f"❌ [Gemini] Erro ao gerar resposta: {e}")
            import traceback
            traceback.print_exc()
            print("⚠️  Usando fallback por segurança")
            motivo_fallback = f"exception:{type(e).__name__}"
            ai_response = HybridAIService._generate_fallback_response(
                found_products,
                user_message,
                session.get_cart_as_list()
            )
            show_cart = False
            used_ai = False

        # 🛠️ CAPTURA DE DADOS FINAL (Sincronizada com o carrinho atualizado)
        final_cart_summary = session.get_cart_summary()
        new_cart_map = {item["product_id"]: item["quantity"] for item in final_cart_summary["items"]}

        # 1. Obter Sugestões
        if gids_sugeridos_fc is not None:
            # F4.2: com function calling, o modelo declara os GIDs explicitamente via
            # sugerir_produtos — não precisamos adivinhar a partir do texto da resposta.
            pool_por_gid = {p["gid"]: p for p in found_products}
            suggested_products = [pool_por_gid[g] for g in gids_sugeridos_fc if g in pool_por_gid]
        else:
            suggested_products = HybridAIService._filter_mentioned_products(ai_response, found_products)

        # 2. Obter Detalhes do Carrinho (cartProducts)
        cart_products = []
        cart_prod_ids = {item["product_id"] for item in final_cart_summary["items"]}

        for cp_id in cart_prod_ids:
            p_from_pool = next((p for p in found_products if p["id"] == cp_id), None)
            if p_from_pool:
                cart_products.append(p_from_pool.copy())
            else:
                full_p = AIService._product_by_id.get(cp_id)
                if full_p:
                    cart_products.append({
                        "id": full_p.id, "gid": getattr(full_p, "gid", ""), "name": full_p.name,
                        "price": float(full_p.price), "restaurant_gid": getattr(full_p, "restaurant_gid", "") or restaurant_gid or "",
                        "image_url": getattr(full_p, "image_url", ""), "description": getattr(full_p, "description", ""),
                        "category": getattr(full_p, "category", ""), "rating": getattr(full_p, "rating", None),
                        "is_popular": getattr(full_p, "is_popular", False), "is_available": getattr(full_p, "is_available", True),
                        "serves_people": getattr(full_p, "serves_people", 1)
                    })

        # 3. ATUALIZAR QUANTIDADES em ambas as listas
        for p in suggested_products:
            p["quantity"] = new_cart_map.get(p["id"], 0)
        for p in cart_products:
            p["quantity"] = new_cart_map.get(p["id"], 0)

        # 4. BUSCAR RESTAURANTES (De todos os produtos envolvidos)
        mentioned_restaurants = []
        seen_res_gids = set()
        all_involved_products = suggested_products + cart_products

        for p in all_involved_products:
            res_gid = p.get("restaurant_gid")
            if res_gid and res_gid not in seen_res_gids:
                res_db = RestaurantRepository.get_by_gid(db, res_gid)
                if res_db:
                    mentioned_restaurants.append({
                        "id": res_db.id, "gid": res_db.gid, "name": res_db.name,
                        "category": res_db.category, "rating": res_db.rating,
                        "image_url": res_db.image_url, "latitude": res_db.latitude,
                        "longitude": res_db.longitude
                    })
                    seen_res_gids.add(res_gid)

        # Salvar resposta da IA no histórico da sessão
        session.add_message("assistant", ai_response)
        session.last_suggested_ids = [p["id"] for p in suggested_products]

        # F4.1: atualiza pessoas/categoria_atual/aguardando para o próximo turno
        HybridAIService._atualizar_contexto_sessao(
            session, user_message, ai_response, intent_info, found_products
        )

        # 🚀 NOTA: Não limpamos mais a sessão aqui. O App chamará a API de confirmação depois.
        SessionManager.save(session)

        print(f"✅ Resposta final gerada (Síncrona)")

        try:
            telemetry.registrar_turno(
                session_id=session.session_id,
                restaurant_gid=restaurant_gid,
                modelo=GeminiSalesAgent._escolher_modelo(context),
                ms_e5=ms_e5,
                ms_pool=None,
                ms_ttft=None,
                ms_total=(_time_module.time() - turn_start) * 1000,
                pool_size=len(found_products),
                tokens_prompt_estimado=GeminiSalesAgent._ultimo_tamanho_prompt // 4,
                tags_emitidas=len(add_to_cart_matches),
                tags_aplicadas=tags_aplicadas,
                tags_descartadas_motivo=tags_descartadas_motivo,
                promessa_de_acao=telemetry.texto_promete_acao_carrinho(ai_response),
                divergencia_de_preco=telemetry.detectar_divergencia_de_preco(ai_response, found_products),
                motivo_fallback=motivo_fallback,
                intent_type=intent_type,
                origem="sync",
                restaurantes_no_carrinho=len(session.restaurantes_no_carrinho()),
                limite_restaurantes_atingido=len(session.restaurantes_no_carrinho()) >= HybridAIService.MAX_RESTAURANTES_POR_PEDIDO,
            )
        except Exception:
            pass  # telemetria nunca deve derrubar a resposta ao usuário

        return {
            "response": ai_response,
            "products": suggested_products,
            "cartProducts": cart_products,
            "restaurantResults": mentioned_restaurants,
            "intent": intent_type,
            "semantic_search_used": True,
            "ai_generated": used_ai,
            "needs_mapped": intent_info["details"],
            "session_id": session.session_id,
            "cart": final_cart_summary,
            "show_cart": show_cart,
            "order_confirmed": False,
            "restaurant_gid": session.restaurant_gid,
        }

    @staticmethod
    def _filter_mentioned_products(ai_response: str, products: List[Dict]) -> List[Dict]:
        """
        Retorna apenas os produtos cujo nome aparece na resposta do Gemini.
        Usa comparação case-insensitive e ignora acentos para maior precisão.
        """
        import unicodedata

        def normalize(text: str) -> str:
            """Remove acentos, converte para minúsculas e limpa espaços"""
            if not text: return ""
            return ''.join(
                c for c in unicodedata.normalize('NFD', text.lower().strip())
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
