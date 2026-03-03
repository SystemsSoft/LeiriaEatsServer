from sentence_transformers import SentenceTransformer, util
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
            return SearchResponse(
                reply="Aqui estão todas as opções disponíveis:",
                intent="show_all",
                restaurantResults=cls._data_cache,  # Retorna a lista completa do cache
                productResults=[]  # Geralmente não listamos TODOS os produtos (seriam centenas)
            )

        # --- 0.5. DETECÇÃO DE NOME EXATO DE RESTAURANTE ---
        # Se o usuário digita exatamente o nome de um restaurante, retornar apenas ele
        query_lower = user_query.lower().strip()
        for restaurant in cls._data_cache:
            if restaurant.name.lower() == query_lower:
                return SearchResponse(
                    reply=f"Encontrei o restaurante {restaurant.name}.",
                    intent="restaurant_search",
                    restaurantResults=[restaurant],
                    productResults=[]
                )

        # --- 1. DETECÇÃO DE INTENÇÃO ---
        intent_mode = cls._detect_intent(user_query, scope)
        price_intent = cls._detect_price_intent(user_query)  # Detectar intenção de preço
        suggestion_mode = "sugestão" in user_query.lower()  # Detectar se é uma busca de sugestões
        quantity = cls._detect_quantity(user_query)  # Detectar quantidade solicitada
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
        return SearchResponse(
            reply=reply,
            intent=intent,
            restaurantResults=final_restaurants,
            productResults=final_products
        )
