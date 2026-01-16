from sentence_transformers import SentenceTransformer, util
from repositories.restaurant_repo import RestaurantRepository
from schemas.models import SearchResponse, Restaurant
from sqlalchemy.orm import Session
import torch


class AIService:
    # Carregamos o modelo na memória apenas UMA VEZ ao iniciar a classe
    # Isso evita travar o servidor a cada requisição
    _model = None
    _embeddings_names = None
    _embeddings_categories = None
    _embeddings_menus = None
    _intent_embeddings = None
    _data_cache = None  # Para guardar a referência dos dados indexados

    INTENT_SHOW_ALL = "Mostrar a lista com todos os restaurantes e opções disponíveis"
    INTENT_SEARCH = "Gostaria de buscar uma comida ou prato específico"

    @classmethod
    def initialize(cls, db: Session):  # <--- Agora pede a Sessão do Banco
        if cls._model is not None:
            return

        print("⏳ services/ai_service: Carregando modelo e lendo dados da AWS...")
        cls._model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')

        cls._intent_embeddings = cls._model.encode([cls.INTENT_SHOW_ALL, cls.INTENT_SEARCH], convert_to_tensor=True)

        # BUSCA DADOS REAIS DO BANCO
        cls._data_cache = RestaurantRepository.get_all(db)  # <--- Passa o DB aqui

        if not cls._data_cache:
            print("⚠️ AVISO: O banco de dados está vazio! A IA não terá o que sugerir.")
        else:
            print(f"✅ Dados Carregados: {len(cls._data_cache)} restaurantes encontrados.")

        cls._index_data(cls._data_cache)
        print("✅ I.A Pronta e Dados Indexados!")

    @classmethod
    def _index_data(cls, restaurants: list[Restaurant]):
        """Cria os embeddings (índices) para busca rápida"""
        names_list = [r.name for r in restaurants]
        categories_list = [r.category for r in restaurants]
        menus_list = []
        for r in restaurants:
            # CORREÇÃO AQUI: Mudamos de r.menu para r.products
            items = ", ".join([f"{p.name} {p.description}" for p in r.products])
            menus_list.append(items)

        cls._embeddings_names = cls._model.encode(names_list, convert_to_tensor=True)
        cls._embeddings_categories = cls._model.encode(categories_list, convert_to_tensor=True)
        cls._embeddings_menus = cls._model.encode(menus_list, convert_to_tensor=True)

    @classmethod
    def process_search(cls, user_query: str) -> SearchResponse:
        # Garante que está inicializado
        if cls._model is None:
            cls.initialize()

        # 0. Comandos Exatos
        comandos_exatos = ["ver todos", "ver tudo", "listar", "all"]
        if user_query.lower() in comandos_exatos:
            return SearchResponse(reply="Aqui estão todas as opções:", intent="show_all", results=cls._data_cache)

        # 1. Roteamento de Intenção
        user_embedding = cls._model.encode(user_query, convert_to_tensor=True)
        intent_scores = util.cos_sim(user_embedding, cls._intent_embeddings)[0]

        if intent_scores[0] > intent_scores[1] and intent_scores[0] > 0.4:
            return SearchResponse(reply="Aqui estão todas as opções:", intent="show_all", results=cls._data_cache)

        # 2. Busca Ponderada
        scores_name = util.cos_sim(user_embedding, cls._embeddings_names)[0]
        scores_category = util.cos_sim(user_embedding, cls._embeddings_categories)[0]
        scores_menu = util.cos_sim(user_embedding, cls._embeddings_menus)[0]

        final_scores = []
        for i in range(len(cls._data_cache)):
            s_name = scores_name[i].item()
            s_cat = scores_category[i].item()
            s_menu = scores_menu[i].item()

            # Peso: Nome (2.0) + Categoria (1.5) + Prato (1.0)
            weighted_score = (s_name * 2.0) + (s_cat * 1.5) + (s_menu * 1.0)
            weighted_score = weighted_score / 4.5
            final_scores.append((weighted_score, cls._data_cache[i]))

        final_scores.sort(key=lambda x: x[0], reverse=True)
        good_matches = [item[1] for item in final_scores if item[0] > 0.25]

        if good_matches:
            top = good_matches[0]
            return SearchResponse(reply=f"A melhor sugestão é o {top.name}.", intent="search_result",
                                  results=good_matches)
        else:
            fallback = [cls._data_cache[0], cls._data_cache[1]]
            return SearchResponse(reply="Não encontrei nada específico, mas veja estas opções:", intent="search_result",
                                  results=fallback)