import datetime
from zoneinfo import ZoneInfo

from sentence_transformers import SentenceTransformer, util

from core.sql_models import RestaurantHourDB
from repositories.restaurant_repo import RestaurantRepository
from schemas.models import SearchResponse, Restaurant, Product
from sqlalchemy.orm import Session
from typing import Optional
import torch
import re



class AIService:
    _model = None
    _data_cache = None  # Lista de objetos Restaurant

    # Índices Especialistas
    _embeddings_names = None
    _embeddings_categories = None
    _embeddings_products = None

    # Mapeamento de Produtos
    _product_obj_cache = []  # Guarda o objeto Product real
    _product_owner_name = []  # Nome do restaurante dono para o 'reply'
    _product_by_id: dict = {}  # {id: Product} — evita O(n) por lookup (F2.2 do plano)
    _restaurant_name_by_product_id: dict = {}  # {id: restaurant_name} — ver Fase 2.3 do PLANO_LIMITE_RESTAURANTES.md
    _restaurantes_aptos_pagamento: set = set()  # GIDs com conta Stripe apta a receber (Fase 0, PLANO_PAGAMENTO_2_ETAPAS.md)

    # Palavras-chave para detecção de intenção de restaurante
    _RESTAURANT_HINTS = {
        "restaurante", "restaurantes", "lugar", "lugares", "onde comer",
        "próximo", "perto", "categoria", "rodízio", "churrascaria", "pizzaria",
        "lanchonete", "estabelecimento", "local"
    }

    # Palavras-chave para detecção de intenção de preço
    _PRICE_HINTS_CHEAP = {
        "barato", "mais barato", "menor preço", "preço baixo", "preço mais baixo",
        "valor baixo", "valor mais baixo", "economia", "econômico", "barateza"
    }

    _PRICE_HINTS_EXPENSIVE = {
        "caro", "mais caro", "maior preço", "preço alto", "preço mais alto",
        "valor alto", "valor mais alto", "premium", "luxo"
    }

    @classmethod
    def get_model(cls):
        if cls._model is None:
            cls._model = SentenceTransformer('intfloat/multilingual-e5-large')
        return cls._model

    @classmethod
    def _detect_intent(cls, user_query: str, scope: Optional[str] = "auto") -> str:
        """
        Detecta a intenção de busca baseado no scope fornecido ou em palavras-chave.

        Args:
            user_query: A consulta do usuário
            scope: Escopo explícito ("product", "restaurant", "both", "auto")

        Returns:
            "product", "restaurant" ou "both"
        """
        s = (scope or "auto").lower().strip()

        # Se o scope foi explicitamente definido, retornar ele
        if s in {"product", "restaurant", "both"}:
            return s

        # Modo auto: detectar pela query
        q = user_query.lower().strip()

        # Se contém palavras-chave de restaurante, retornar "restaurant"
        if any(hint in q for hint in cls._RESTAURANT_HINTS):
            return "restaurant"

        # Padrão: priorizar produto
        return "product"

    @classmethod
    def _detect_price_intent(cls, user_query: str) -> Optional[str]:
        """
        Detecta se a busca contém intenção de encontrar produtos por preço.

        Args:
            user_query: A consulta do usuário

        Returns:
            "cheap" (preço baixo), "expensive" (preço alto) ou None (sem intenção de preço)
        """
        q = user_query.lower().strip()

        # Verificar se contém palavras-chave de preço baixo
        if any(hint in q for hint in cls._PRICE_HINTS_CHEAP):
            return "cheap"

        # Verificar se contém palavras-chave de preço alto
        if any(hint in q for hint in cls._PRICE_HINTS_EXPENSIVE):
            return "expensive"

        # Sem intenção de preço
        return None

    @classmethod
    def _detect_quantity(cls, user_query: str) -> int:
        """
        Detecta se há quantidade numérica na pesquisa (ex: "2 pizzas", "três refrigerantes").

        Args:
            user_query: A consulta do usuário

        Returns:
            Quantidade encontrada ou 1 (padrão)
        """
        q = user_query.lower().strip()

        # Dicionário de números por extenso
        word_numbers = {
            "um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3,
            "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9,
            "dez": 10, "onze": 11, "doze": 12, "treze": 13, "quatorze": 14,
            "quinze": 15, "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50
        }

        # Procurar por números escritos por extenso
        for word, num in word_numbers.items():
            if word in q:
                return num

        # Procurar por números digitados (ex: "2 pizzas", "3 refrigerantes")
        match = re.search(r'\b(\d+)\b', q)
        if match:
            return int(match.group(1))

        # Sem quantidade especificada
        return 1

    @classmethod
    def _parse_multiple_products(cls, user_query: str):
        """
        Detecta se há múltiplos produtos na query e os separa.
        Suporta qualquer quantidade de produtos.

        Args:
            user_query: A consulta do usuário

        Returns:
            Lista de dicionários com 'text' e 'quantity' para cada produto, ou None se não detectar múltiplos
        """
        q = user_query.lower().strip()
        connectors = [" e ", " com ", " mais ", ", "]

        # Verificar se há algum conector
        has_connector = any(conn in q for conn in connectors)
        if not has_connector:
            return None

        # Remover palavras de comando no início
        remove_patterns = [
            r'^quero\s+', r'^preciso\s+', r'^queria\s+', r'^gostaria\s+',
            r'^me\s+traz\s+', r'^pode\s+trazer\s+', r'^vou\s+querer\s+'
        ]
        for pattern in remove_patterns:
            q = re.sub(pattern, '', q, flags=re.IGNORECASE)

        # Dividir por conectores (incluindo vírgula)
        parts = re.split(r'\s+e\s+|\s+com\s+|\s+mais\s+|,\s*', q)

        if len(parts) < 2:
            return None

        products = []
        for part in parts:
            part = part.strip()
            if not part:
                continue

            # Extrair quantidade do fragmento
            quantity = 1
            word_numbers = {
                "um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3,
                "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9,
                "dez": 10
            }

            # Procurar números por extenso
            for word, num in word_numbers.items():
                if part.startswith(word + " "):
                    quantity = num
                    part = part[len(word):].strip()
                    break

            # Procurar números digitados
            match = re.match(r'^(\d+)\s+', part)
            if match:
                quantity = int(match.group(1))
                part = part[match.end():].strip()

            products.append({
                "text": part,
                "quantity": quantity
            })

        return products if len(products) >= 2 else None

    @classmethod
    def _search_product_in_restaurant(cls, product_query: str, restaurant_id: int, model) -> Optional[Product]:
        """
        Busca um produto específico dentro de um restaurante.

        Args:
            product_query: Nome/descrição do produto a buscar
            restaurant_id: ID do restaurante onde buscar
            model: Modelo de embeddings

        Returns:
            Produto encontrado ou None
        """
        # Encontrar o restaurante no cache
        target_restaurant = None
        for restaurant in cls._data_cache:
            if restaurant.id == restaurant_id:
                target_restaurant = restaurant
                break

        if not target_restaurant or not target_restaurant.products:
            return None

        # Criar embeddings dos produtos deste restaurante
        product_texts = []
        products = []
        for p in target_restaurant.products:
            text = f"passage: {p.name} {p.description if p.description else ''}"
            product_texts.append(text)
            products.append(p)

        if not product_texts:
            return None

        # Fazer embedding da query
        query_embedding = model.encode(f"query: {product_query}", convert_to_tensor=True)
        product_embeddings = model.encode(product_texts, convert_to_tensor=True)

        # Calcular similaridades
        scores = util.cos_sim(query_embedding, product_embeddings)[0]

        # Encontrar o produto com maior score
        best_idx = scores.argmax().item()
        best_score = scores[best_idx].item()

        # Retornar apenas se score for suficiente (threshold 0.60 para busca interna)
        if best_score > 0.60:
            return products[best_idx]

        return None

    @classmethod
    def _process_multiple_products_search(cls, products_list: list, db: Session, model) -> SearchResponse:
        """
        Processa busca de múltiplos produtos (2 ou mais), validando se todos existem no mesmo restaurante.

        Args:
            products_list: Lista de dicionários com 'text' e 'quantity'
            db: Sessão do banco de dados
            model: Modelo de embeddings

        Returns:
            SearchResponse com produtos encontrados ou mensagem de erro
        """
        if not products_list or len(products_list) < 2:
            return SearchResponse(
                reply="Não consegui identificar os produtos da sua pesquisa.",
                intent="no_match",
                restaurantResults=[],
                productResults=[]
            )

        # 1. Buscar o primeiro produto globalmente
        first_product_query = products_list[0]["text"]
        first_quantity = products_list[0]["quantity"]

        query_embedding = model.encode(f"query: {first_product_query}", convert_to_tensor=True)

        # Buscar nos produtos indexados
        if cls._embeddings_products is None:
            return SearchResponse(
                reply="Não há produtos disponíveis no momento.",
                intent="no_match",
                restaurantResults=[],
                productResults=[]
            )

        scores_prod = util.cos_sim(query_embedding, cls._embeddings_products)[0]
        best_idx = scores_prod.argmax().item()
        best_score = scores_prod[best_idx].item()

        if best_score <= 0.65:
            return SearchResponse(
                reply=f"Não encontrei '{first_product_query}' no cardápio.",
                intent="no_match",
                restaurantResults=[],
                productResults=[]
            )

        # Primeiro produto encontrado
        first_product = cls._product_obj_cache[best_idx]
        restaurant_id = first_product.restaurant_id
        restaurant_name = cls._product_owner_name[best_idx]

        # Copiar o produto para não modificar o cache
        from copy import copy
        first_product = copy(first_product)
        first_product.quantity = first_quantity

        found_products = [first_product]

        # 2. Buscar TODOS os demais produtos no mesmo restaurante (loop genérico)
        for i in range(1, len(products_list)):
            product_query = products_list[i]["text"]
            product_quantity = products_list[i]["quantity"]

            # Buscar o produto no restaurante específico
            found_product = cls._search_product_in_restaurant(product_query, restaurant_id, model)

            if found_product is None:
                # Produto não encontrado - retornar todos os produtos encontrados até agora
                found_info = ", ".join([f"{p.quantity}x {p.name}" for p in found_products])
                return SearchResponse(
                    reply=f"O restaurante {restaurant_name} não tem '{product_query}' disponível no momento. Encontrei apenas: {found_info}.",
                    intent="product_not_available",
                    restaurantResults=[],
                    productResults=found_products  # Retorna todos os produtos encontrados até agora
                )

            # Copiar o produto e adicionar quantidade
            found_product = copy(found_product)
            found_product.quantity = product_quantity
            found_products.append(found_product)

        # 3. Todos os produtos foram encontrados no mesmo restaurante
        products_info = ", ".join([f"{p.quantity}x {p.name}" for p in found_products])
        total_price = sum(p.price * p.quantity for p in found_products)

        reply = f"Encontrei no {restaurant_name}: {products_info} (Total: R$ {total_price:.2f})."

        return SearchResponse(
            reply=reply,
            intent="multiple_products_found",
            restaurantResults=[],
            productResults=found_products
        )


    @classmethod
    def _annotate_is_closed(cls, restaurants: list, db: Session) -> list:
        """
        Para cada restaurante (RestaurantDB ou Restaurant), consulta a tabela
        restaurant_hours pelo dia da semana atual e devolve uma lista de objetos
        Restaurant (Pydantic) com o campo is_closed preenchido.
        """
        # Dia da semana calculado na timezone de Portugal (não do servidor EUA)
        # Python weekday() 0=Segunda...6=Domingo
        # Tabela usa:       0=Domingo...6=Sábado
        _lisbon_tz = ZoneInfo("Europe/Lisbon")
        today_lisbon = datetime.datetime.now(datetime.timezone.utc).astimezone(_lisbon_tz)
        today_python = today_lisbon.weekday()
        today_db = (today_python + 1) % 7

        result = []
        for restaurant in restaurants:
            # Consultar o registo de horário para o dia actual
            hour_record = (
                db.query(RestaurantHourDB)
                .filter(
                    RestaurantHourDB.restaurant_id == restaurant.id,
                    RestaurantHourDB.day_of_week == today_db
                )
                .first()
            )

            is_closed_value = bool(hour_record.is_closed) if hour_record is not None else None
            raw_val = hour_record.is_closed if hour_record else 'N/A'
            print(f"[DEBUG] restaurant_id={restaurant.id} day={today_db} "
                  f"is_closed_raw={raw_val!r} "
                  f"-> {is_closed_value}")

            # Construir objecto Pydantic explicitamente para garantir is_closed correcto
            from schemas.models import Product as ProductSchema
            pydantic_restaurant = Restaurant(
                id=restaurant.id,
                name=restaurant.name,
                category=restaurant.category,
                rating=restaurant.rating,
                image_url=restaurant.image_url,
                plan=getattr(restaurant, "plan", None),
                is_closed=is_closed_value,
                latitude=getattr(restaurant, "latitude", None),
                longitude=getattr(restaurant, "longitude", None),
                gid=getattr(restaurant, "gid", None),
                products=[
                    ProductSchema.model_validate(p) if hasattr(p, '__table__')
                    else p
                    for p in (restaurant.products or [])
                ]
            )
            result.append(pydantic_restaurant)

        return result

    @classmethod
    def reload_data(cls, db: Session):
        data = RestaurantRepository.get_all(db)
        if not data:
            cls._data_cache = []
            return
        cls._data_cache = data
        cls._index_data(data)

    @staticmethod
    def _texto_para_indice(p, categoria_restaurante: str) -> str:
        """
        Texto usado no embedding do produto (F1.1 do PLANO_EXECUCAO_IA.md).

        Antes usava apenas nome + descrição, deixando de fora ingredients, dietary_tags,
        search_tags, recommended_for e spice_level — colunas que existem no banco
        exatamente para a IA e que ficavam sem nenhum sinal no vetor. Consultas como
        "algo sem lactose" ou "picante para o jantar" não tinham como casar com nada.
        """
        partes = [
            getattr(p, "name", None),
            getattr(p, "description", None),
            getattr(p, "category", None),
            categoria_restaurante,
            getattr(p, "ingredients", None),
            getattr(p, "dietary_tags", None),
            getattr(p, "search_tags", None),
            getattr(p, "recommended_for", None),
            getattr(p, "portion_size", None),
        ]
        spice = getattr(p, "spice_level", None)
        if spice and spice != "não picante":
            partes.append(spice)
        return "passage: " + " | ".join(str(x) for x in partes if x)

    @staticmethod
    def _normalizar_texto_busca(texto: Optional[str]) -> str:
        """Minúsculas e sem acentos, para comparação léxica tolerante a "Francesinha"
        vs. "francesinha" vs. "fráncesinha" (erro de digitação comum em mobile)."""
        import unicodedata
        if not texto:
            return ""
        sem_acento = ''.join(
            c for c in unicodedata.normalize('NFD', texto.lower())
            if unicodedata.category(c) != 'Mn'
        )
        return sem_acento.strip()

    # Palavras genéricas de pedido/conectores que não carregam sinal de relevância para
    # casamento léxico — sem isso, "quero um produto" casaria com QUALQUER produto só
    # pela palavra "produto", inflando falsos positivos no sinal léxico do F5.2.
    _STOPWORDS_LEXICAL = {
        "quero", "queria", "gostaria", "preciso", "vou", "querer", "traz", "trazer",
        "pode", "por", "favor", "para", "com", "sem", "uma", "uns", "umas", "que",
        "produto", "produtos", "prato", "pratos", "item", "itens", "algo", "alguma",
        "algum", "coisa", "tem", "tens", "temos", "cardapio", "cardápio", "menu",
    }

    @classmethod
    def _score_lexical(cls, query_normalizada: str, produto) -> float:
        """
        F5.2 do PLANO_EXECUCAO_IA.md — sinal léxico para fundir com o score vetorial via
        RRF. Cobre o caso em que o embedding multilingual erra: nomes próprios de prato
        ("Francesinha", "Bitoque", "Combo do Chefe") tendem a ter score de cosseno
        mediano porque o modelo não tem uma associação semântica forte para eles — mas
        um match textual direto é trivial de pegar.

        1.0  → a consulta inteira aparece como substring no nome/tags do produto
        (0,1] → fração dos tokens de conteúdo (>2 letras, fora de _STOPWORDS_LEXICAL)
                da consulta que aparecem no produto
        0.0  → nenhuma sobreposição
        """
        if not query_normalizada:
            return 0.0
        texto_produto = cls._normalizar_texto_busca(
            f"{getattr(produto, 'name', '') or ''} {getattr(produto, 'search_tags', '') or ''}"
        )
        if not texto_produto:
            return 0.0
        if query_normalizada in texto_produto:
            return 1.0

        tokens_query = {
            t for t in query_normalizada.split()
            if len(t) > 2 and t not in cls._STOPWORDS_LEXICAL
        }
        if not tokens_query:
            return 0.0
        tokens_produto = set(texto_produto.split())
        intersecao = tokens_query & tokens_produto
        if not intersecao:
            return 0.0
        return len(intersecao) / len(tokens_query)

    @staticmethod
    def _rrf(rank_a: Optional[int], rank_b: Optional[int], k: int = 60) -> float:
        """Reciprocal Rank Fusion — funde duas ordenações (vetorial e léxica) sem
        precisar normalizar escalas de score diferentes entre si. rank_a/rank_b são
        posições 0-based; None significa "não apareceu nessa lista"."""
        score = 0.0
        if rank_a is not None:
            score += 1.0 / (k + rank_a)
        if rank_b is not None:
            score += 1.0 / (k + rank_b)
        return score

    @classmethod
    def _index_data(cls, restaurants: list[Restaurant]):
        model = cls.get_model()

        # --- PARTE 1: Especialista em Restaurantes (Nomes e Categorias) ---
        names = [f"passage: {r.name}" for r in restaurants]
        categories = [f"passage: {r.category}" for r in restaurants]
        embeddings_names = model.encode(names, convert_to_tensor=True)
        embeddings_categories = model.encode(categories, convert_to_tensor=True)

        # --- PARTE 2: Especialista em Produtos ---
        product_texts = []
        product_obj_cache = []
        product_owner_name = []
        product_by_id = {}
        restaurant_name_by_product_id = {}

        # Fase 0 do PLANO_PAGAMENTO_2_ETAPAS.md: restaurante só entra aqui se tiver conta
        # Stripe capaz de RECEBER dinheiro. Usa stripe_onboarding_completed (não `status`)
        # porque a pergunta é estritamente "esta conta consegue receber pagamento?" — o
        # `status` é a situação comercial, um conceito mais amplo, e já é sincronizado com
        # este campo pelo webhook account.updated.
        restaurantes_aptos_pagamento = {
            r.gid for r in restaurants
            if getattr(r, "gid", None) and getattr(r, "stripe_account_id", None)
               and getattr(r, "stripe_onboarding_completed", False)
        }

        for r in restaurants:
            for p in r.products:
                text = cls._texto_para_indice(p, r.category)
                product_texts.append(text)
                product_obj_cache.append(p)
                product_owner_name.append(r.name)
                product_by_id[p.id] = p
                # {product_id: restaurant_name} — o pool de produtos do chat mistura
                # objetos ORM (ProductDB, com `.restaurant.name`) e Pydantic (Product,
                # sem esse campo). Este dict dá uma forma única de obter o nome do
                # restaurante independente do tipo do objeto no pool (ver
                # PLANO_LIMITE_RESTAURANTES.md, Fase 2.3).
                restaurant_name_by_product_id[p.id] = r.name

        embeddings_products = model.encode(product_texts, convert_to_tensor=True) if product_texts else None

        # Rebind atômico ao final: requests concorrentes que estejam lendo essas
        # estruturas durante a reindexação não devem ver um estado parcial (ex.: cache
        # de objetos já trocado mas embeddings ainda antigos, ou vice-versa) — isso
        # fazia um lookup falhar em silêncio e uma tag ADD_TO_CART do Gemini ser
        # descartada no meio de uma reindexação.
        cls._embeddings_names = embeddings_names
        cls._embeddings_categories = embeddings_categories
        cls._product_obj_cache = product_obj_cache
        cls._product_owner_name = product_owner_name
        cls._product_by_id = product_by_id
        cls._restaurant_name_by_product_id = restaurant_name_by_product_id
        cls._restaurantes_aptos_pagamento = restaurantes_aptos_pagamento
        cls._embeddings_products = embeddings_products

    @classmethod
    def process_search(cls, user_query: str, db: Session, scope: Optional[str] = "auto") -> SearchResponse:
        if cls._data_cache is None:
            cls.reload_data(db)

        if not cls._data_cache:
            return SearchResponse(
                reply="Sem dados no momento.",
                intent="empty",
                restaurantResults=[],
                productResults=[]
            )

        model = cls.get_model()

        # --- 0. LÓGICA DE ATALHOS (VER TODOS) ---
        # Definimos o que é considerado um comando para listar tudo
        shortcuts = ["ver todos", "tudo", "restaurantes", "mostrar todos", "lista"]
        if user_query.lower().strip() in shortcuts:
            all_restaurants = cls._annotate_is_closed(list(cls._data_cache), db)
            return SearchResponse(
                reply="Aqui estão todas as opções disponíveis:",
                intent="show_all",
                restaurantResults=all_restaurants,
                productResults=[]
            )

        # --- 0.5. DETECÇÃO DE NOME EXATO DE RESTAURANTE ---
        # Se o usuário digita exatamente o nome de um restaurante, retornar apenas ele
        query_lower = user_query.lower().strip()
        for restaurant in cls._data_cache:
            if restaurant.name.lower() == query_lower:
                annotated = cls._annotate_is_closed([restaurant], db)
                return SearchResponse(
                    reply=f"Encontrei o restaurante {restaurant.name}.",
                    intent="restaurant_search",
                    restaurantResults=annotated,
                    productResults=[]
                )

        # --- 1. DETECÇÃO DE INTENÇÃO ---
        intent_mode = cls._detect_intent(user_query, scope)
        price_intent = cls._detect_price_intent(user_query)  # Detectar intenção de preço
        suggestion_mode = "sugestão" in user_query.lower()  # Detectar se é uma busca de sugestões
        quantity = cls._detect_quantity(user_query)  # Detectar quantidade solicitada

        # --- 1.5. DETECÇÃO DE MÚLTIPLOS PRODUTOS ---
        multiple_products = cls._parse_multiple_products(user_query)
        if multiple_products:
            # Processar busca de múltiplos produtos
            return cls._process_multiple_products_search(multiple_products, db, model)

        user_embedding = model.encode(f"query: {user_query}", convert_to_tensor=True)

        # --- 2. BUSCA DE RESTAURANTES (apenas para intenção explícita) ---
        res_results = []
        if intent_mode in {"restaurant", "both"}:
            scores_name = util.cos_sim(user_embedding, cls._embeddings_names)[0]
            scores_cat = util.cos_sim(user_embedding, cls._embeddings_categories)[0]

            for i, res in enumerate(cls._data_cache):
                # Score ponderado: 70% nome + 30% categoria
                score_name_only = scores_name[i].item()
                score = (0.7 * score_name_only) + (0.3 * scores_cat[i].item())

                if score > 0.45:
                    res_results.append({"obj": res, "score": score})

        # --- 3. BUSCA DE PRODUTOS (sempre executada) ---
        # F5.2: busca híbrida (vetorial E5 + léxica) fundida por Reciprocal Rank Fusion.
        # Nome próprio de prato ("Francesinha", "Bitoque", "Combo do Chefe") é onde o
        # embedding multilingual é mais fraco e onde um match textual acerta na hora.
        # O plano original previa pg_trgm (Postgres), mas core/database.py usa
        # mysql+pymysql — o banco real é MySQL. Em vez de depender de um recurso
        # específico de dialeto (FULLTEXT do MySQL exigiria migração e não pôde ser
        # validado contra o banco real nesta sessão), o lado léxico roda em Python sobre
        # o cache já carregado em memória — portátil entre bancos, sem migração, e
        # barato para catálogos deste porte (dezenas/centenas de produtos por busca).
        prod_results = []
        if cls._embeddings_products is not None:
            scores_prod = util.cos_sim(user_embedding, cls._embeddings_products)[0]
            query_normalizada = cls._normalizar_texto_busca(user_query)

            # F5.1 (corte relativo) aplicado ao cosseno cru, ANTES da fusão: RRF é um
            # score baseado em posição/rank, não em distância — a proporção entre o 1º e
            # o 6º colocado por rank muda pouco mesmo quando a diferença de relevância
            # real é grande. Aplicar um corte relativo em cima do score RRF não filtraria
            # quase nada; o corte que faz sentido continua sendo sobre o cosseno.
            melhor_score_vetorial = scores_prod.max().item() if len(scores_prod) else 0.0
            PROD_SCORE_RELATIVE_CUTOFF = 0.97
            piso_vetorial_relativo = melhor_score_vetorial * PROD_SCORE_RELATIVE_CUTOFF

            rank_vetorial = {}
            for idx in sorted(range(len(cls._product_obj_cache)),
                               key=lambda i: scores_prod[i].item(), reverse=True):
                rank_vetorial[idx] = len(rank_vetorial)

            scores_lexicais = [
                cls._score_lexical(query_normalizada, p) for p in cls._product_obj_cache
            ]
            rank_lexical = {}
            for idx in sorted(
                (i for i, s in enumerate(scores_lexicais) if s > 0),
                key=lambda i: scores_lexicais[i], reverse=True,
            ):
                rank_lexical[idx] = len(rank_lexical)

            for i, p_obj in enumerate(cls._product_obj_cache):
                score_vetorial = scores_prod[i].item()
                tem_match_lexical = i in rank_lexical
                # Um produto entra na disputa se: (a) semanticamente próximo do melhor
                # resultado vetorial (corte relativo, F5.1), OU (b) tem match léxico —
                # é essa segunda condição que resgata nomes próprios de prato que o
                # embedding subestimou (ex.: "Francesinha" com score vetorial mediano).
                if score_vetorial < piso_vetorial_relativo and not tem_match_lexical:
                    continue
                score_fundido = cls._rrf(rank_vetorial.get(i), rank_lexical.get(i))
                prod_results.append({
                    "obj": p_obj,
                    "score": score_fundido,
                    "score_vetorial": score_vetorial,
                    "owner": cls._product_owner_name[i],
                })

        # --- 4. ORDENAÇÃO ---
        res_results.sort(key=lambda x: x["score"], reverse=True)

        # Ordenar produtos pelo score fundido (RRF) — o corte por relevância (F5.1) já
        # foi aplicado acima, sobre o cosseno cru; aqui só falta truncar por quantidade.
        prod_results.sort(key=lambda x: x["score"], reverse=True)
        prod_results = prod_results[:6]

        # Se detectou intenção de preço, ordenar produtos por preço
        if price_intent == "cheap":
            # Ordenar por preço crescente (mais baratos primeiro)
            prod_results.sort(key=lambda x: x["obj"].price)
        elif price_intent == "expensive":
            # Ordenar por preço decrescente (mais caros primeiro)
            prod_results.sort(key=lambda x: x["obj"].price, reverse=True)
        else:
            # Ordenar por score de similaridade
            prod_results.sort(key=lambda x: x["score"], reverse=True)

        # --- 5. LÓGICA DE RETORNO POR INTENÇÃO ---
        if intent_mode == "restaurant":
            # Comportamento 1: Retornar apenas restaurantes
            final_restaurants = [item["obj"] for item in res_results[:10]]
            final_products = []
            if final_restaurants:
                reply = f"Encontrei restaurantes como {final_restaurants[0].name}."
                intent = "restaurant_search"
            else:
                reply = "Não encontrei restaurantes relevantes para essa busca."
                intent = "no_match"

        elif intent_mode == "both":
            # Comportamento 2: Pode retornar ambos, priorizar o de melhor score
            best_restaurant_score = res_results[0]["score"] if res_results else 0.0
            best_product_score = prod_results[0]["score"] if prod_results else 0.0

            if best_restaurant_score > best_product_score and best_restaurant_score > 0:
                final_restaurants = [item["obj"] for item in res_results[:10]]
                final_products = []
                reply = f"Encontrei restaurantes como {final_restaurants[0].name}."
                intent = "restaurant_search"
            elif best_product_score > 0:
                final_restaurants = []
                final_products = [item["obj"] for item in prod_results[:1]]
                reply = f"Encontrei pratos como {final_products[0].name}."
                intent = "product_search"
            else:
                final_restaurants = []
                final_products = []
                reply = "Não encontrei resultados relevantes para essa busca."
                intent = "no_match"

        else:
            # Comportamento 3 (product): Retornar APENAS produtos
            final_restaurants = []

            # Retornar sempre os top 6 produtos mais relevantes
            # O Gemini decide quais mencionar baseado no contexto
            final_products = [item["obj"] for item in prod_results[:6]]

            # Adicionar quantidade ao primeiro produto
            if final_products:
                final_products[0].quantity = quantity

            if final_products:
                # Criar reply baseado nos produtos encontrados
                produtos_info = ", ".join([f"{p.name} (R$ {p.price:.2f})" for p in final_products])
                if price_intent == "cheap":
                    reply = f"O prato mais barato: {quantity}x {final_products[0].name} (R$ {final_products[0].price * quantity:.2f})."
                elif price_intent == "expensive":
                    reply = f"O prato premium: {quantity}x {final_products[0].name} (R$ {final_products[0].price * quantity:.2f})."
                elif len(final_products) > 1:
                    reply = f"Encontrei {len(final_products)} opções: {produtos_info}."
                else:
                    reply = f"Encontrei: {quantity}x {final_products[0].name} (R$ {final_products[0].price * quantity:.2f})."
                intent = "product_search"
            else:
                reply = "Não encontrei pratos relevantes para essa busca."
                intent = "no_match"

        # --- 6. RETORNO ---
        if final_restaurants:
            final_restaurants = cls._annotate_is_closed(final_restaurants, db)

        return SearchResponse(
            reply=reply,
            intent=intent,
            restaurantResults=final_restaurants,
            productResults=final_products
        )
