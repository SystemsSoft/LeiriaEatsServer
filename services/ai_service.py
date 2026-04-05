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

    @classmethod
    def _index_data(cls, restaurants: list[Restaurant]):
        model = cls.get_model()

        # --- PARTE 1: Especialista em Restaurantes (Nomes e Categorias) ---
        names = [f"passage: {r.name}" for r in restaurants]
        categories = [f"passage: {r.category}" for r in restaurants]
        cls._embeddings_names = model.encode(names, convert_to_tensor=True)
        cls._embeddings_categories = model.encode(categories, convert_to_tensor=True)

        # --- PARTE 2: Especialista em Produtos ---
        product_texts = []
        cls._product_obj_cache = []
        cls._product_owner_name = []

        for r in restaurants:
            for p in r.products:
                # Indexamos com prefixo 'passage:' + Nome + Descrição para maior profundidade
                text = f"passage: {p.name} {p.description if p.description else ''}"
                product_texts.append(text)
                cls._product_obj_cache.append(p)
                cls._product_owner_name.append(r.name)

        if product_texts:
            cls._embeddings_products = model.encode(product_texts, convert_to_tensor=True)

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
        prod_results = []
        if cls._embeddings_products is not None:
            scores_prod = util.cos_sim(user_embedding, cls._embeddings_products)[0]
            for i, p_obj in enumerate(cls._product_obj_cache):
                score = scores_prod[i].item()
                if score > 0.65:  # Threshold aumentado para maior precisão
                    prod_results.append({
                        "obj": p_obj,
                        "score": score,
                        "owner": cls._product_owner_name[i]
                    })

        # --- 4. ORDENAÇÃO ---
        res_results.sort(key=lambda x: x["score"], reverse=True)

        # Regra: se NOT em modo sugestão, manter apenas o produto de maior score
        # Se em modo sugestão, manter TODOS os produtos acima de 0.65
        if not suggestion_mode and len(prod_results) > 1:
            best_product = max(prod_results, key=lambda x: x["score"])
            prod_results = [best_product]

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

            # Se em modo sugestão, retornar todos os produtos qualificados (até 10)
            if suggestion_mode:
                final_products = [item["obj"] for item in prod_results[:10]]
            else:
                # Caso contrário, retornar apenas 1
                final_products = [item["obj"] for item in prod_results[:1]]

            # Adicionar quantidade ao produto(s)
            if final_products and not suggestion_mode:
                # Para busca normal, adicionar quantidade ao produto único
                final_products[0].quantity = quantity
            elif final_products and suggestion_mode:
                # Para sugestões, adicionar quantidade ao primeiro sugerido
                final_products[0].quantity = quantity

            if final_products:
                # Criar reply mais específico baseado na intenção de preço e modo
                if suggestion_mode:
                    # Modo sugestão: listar produtos como sugestões
                    produtos_info = ", ".join([f"{p.name} (R$ {p.price:.2f})" for p in final_products])
                    reply = f"Aqui estão sugestões de pratos: {produtos_info}."
                elif price_intent == "cheap":
                    # Mostrar apenas o 1 prato mais barato
                    reply = f"Encontrei o prato mais barato: {quantity}x {final_products[0].name} (R$ {final_products[0].price * quantity:.2f})."
                elif price_intent == "expensive":
                    # Mostrar apenas o 1 prato mais caro
                    reply = f"Encontrei o prato premium: {quantity}x {final_products[0].name} (R$ {final_products[0].price * quantity:.2f})."
                else:
                    reply = f"Encontrei o prato: {quantity}x {final_products[0].name} (R$ {final_products[0].price * quantity:.2f})."
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
