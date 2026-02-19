from sentence_transformers import SentenceTransformer, util
from repositories.restaurant_repo import RestaurantRepository
from schemas.models import SearchResponse, Restaurant, Product
from sqlalchemy.orm import Session
import torch


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

    @classmethod
    def get_model(cls):
        if cls._model is None:
            cls._model = SentenceTransformer('intfloat/multilingual-e5-large')
        return cls._model

    @classmethod
    def _index_data(cls, restaurants: list[Restaurant]):
        model = cls.get_model()

        # --- PARTE 1: Restaurantes ---
        # Adicionado prefixo 'passage: ' em ambos
        names = [f"passage: {r.name}" for r in restaurants]
        categories = [f"passage: {r.category}" for r in restaurants]

        cls._embeddings_names = model.encode(names, convert_to_tensor=True)
        cls._embeddings_categories = model.encode(categories, convert_to_tensor=True)

        # --- PARTE 2: Produtos ---
        product_texts = []
        cls._product_obj_cache = []
        cls._product_owner_name = []

        for r in restaurants:
            for p in r.products:
                # ADICIONADO o prefixo 'passage: ' aqui também!
                text = f"passage: {p.name} {p.description if p.description else ''}"
                product_texts.append(text)
                cls._product_obj_cache.append(p)
                cls._product_owner_name.append(r.name)

        if product_texts:
            cls._embeddings_products = model.encode(product_texts, convert_to_tensor=True)

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
        categories = [r.category for r in restaurants]
        cls._embeddings_names = model.encode(names, convert_to_tensor=True)
        cls._embeddings_categories = model.encode(categories, convert_to_tensor=True)

        # --- PARTE 2: Especialista em Produtos ---
        product_texts = []
        cls._product_obj_cache = []
        cls._product_owner_name = []

        for r in restaurants:
            for p in r.products:
                # Indexamos Nome + Descrição para maior profundidade
                product_texts.append(f"{p.name} {p.description}")
                cls._product_obj_cache.append(p)
                cls._product_owner_name.append(r.name)

        if product_texts:
            cls._embeddings_products = model.encode(product_texts, convert_to_tensor=True)

    @classmethod
    def process_search(cls, user_query: str, db: Session) -> SearchResponse:
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

        # --- 1. DETECÇÃO DE INTENÇÃO POR IA (Opcional mas recomendado) ---
        user_embedding = model.encode(f"query: {user_query}", convert_to_tensor=True)


        res_results = []
        scores_name = util.cos_sim(user_embedding, cls._embeddings_names)[0]
        scores_cat = util.cos_sim(user_embedding, cls._embeddings_categories)[0]

        for i, res in enumerate(cls._data_cache):
            score = (scores_name[i].item() + scores_cat[i].item()) / 2
            if score > 0.35:
                res_results.append({"obj": res, "score": score})

        # --- 3. BUSCA DE PRODUTOS ---
        prod_results = []
        if cls._embeddings_products is not None:
            scores_prod = util.cos_sim(user_embedding, cls._embeddings_products)[0]
            for i, p_obj in enumerate(cls._product_obj_cache):
                score = scores_prod[i].item()
                if score > 0.45:
                    prod_results.append({
                        "obj": p_obj,
                        "score": score,
                        "owner": cls._product_owner_name[i]
                    })

        # --- 4. ORDENAÇÃO E RETORNO ---
        res_results.sort(key=lambda x: x["score"], reverse=True)
        prod_results.sort(key=lambda x: x["score"], reverse=True)

        final_restaurants = [item["obj"] for item in res_results[:10]]
        final_products = [item["obj"] for item in prod_results[:10]]

        # Se a busca falhou em ambos, mas o usuário não usou atalho
        if not final_restaurants and not final_products:
            return SearchResponse(
                reply="Não encontrei nada específico, mas veja estes restaurantes:",
                intent="no_match",
                restaurantResults=cls._data_cache[:3],  # Sugere os 3 primeiros
                productResults=[]
            )

        # Resposta dinâmica para voz
        reply = "Encontrei estas opções para você."
        if final_products and final_restaurants:
            reply = f"Encontrei o prato '{prod_results[0]['obj'].name}' e também o restaurante {final_restaurants[0].name}."
        elif final_products:
            reply = f"Encontrei alguns pratos, como o {prod_results[0]['obj'].name} do {prod_results[0]['owner']}."

        return SearchResponse(
            reply=reply,
            intent="search_result",
            restaurantResults=final_restaurants,
            productResults=final_products
        )