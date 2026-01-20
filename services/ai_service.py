# Arquivo: services/ai_service.py
from sentence_transformers import SentenceTransformer, util
from repositories.restaurant_repo import RestaurantRepository
from schemas.models import SearchResponse, Restaurant
from sqlalchemy.orm import Session
import torch


class AIService:
    _model = None
    _embeddings_names = None
    _embeddings_categories = None
    _embeddings_menus = None
    _intent_embeddings = None
    _data_cache = None

    INTENT_SHOW_ALL = "Mostrar a lista com todos os restaurantes e opções disponíveis"
    INTENT_SEARCH = "Gostaria de buscar uma comida ou prato específico"

    @classmethod
    def get_model(cls):
        # Garante que o modelo pesado só carrega uma vez
        if cls._model is None:
            print("⏳ AI: Carregando modelo SentenceTransformer...")
            cls._model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
            cls._intent_embeddings = cls._model.encode([cls.INTENT_SHOW_ALL, cls.INTENT_SEARCH], convert_to_tensor=True)
        return cls._model

    @classmethod
    def reload_data(cls, db: Session):
        """Força a I.A a ler o banco de dados novamente (útil após cadastro de produtos)"""
        print("🔄 AI: Atualizando índice de busca com dados do banco...")
        data = RestaurantRepository.get_all(db)

        if not data:
            print("⚠️ AVISO: Banco vazio.")
            cls._data_cache = []
            return

        cls._data_cache = data
        cls._index_data(data)
        print(f"✅ AI: Índice atualizado com {len(data)} restaurantes.")

    @classmethod
    def _index_data(cls, restaurants: list[Restaurant]):
        model = cls.get_model()  # Garante que modelo existe

        names_list = [r.name for r in restaurants]
        categories_list = [r.category for r in restaurants]

        menus_list = []
        for r in restaurants:
            items = ", ".join([f"{p.name} ({p.description})" for p in r.products])
            menus_list.append(items if items else "Sem cardápio")

        cls._embeddings_names = model.encode(names_list, convert_to_tensor=True)
        cls._embeddings_categories = model.encode(categories_list, convert_to_tensor=True)
        cls._embeddings_menus = model.encode(menus_list, convert_to_tensor=True)

    @classmethod
    def process_search(cls, user_query: str, db: Session) -> SearchResponse:
        # Se for a primeira vez ou se o cache estiver vazio, carrega os dados
        if cls._data_cache is None:
            cls.reload_data(db)

        # Se mesmo recarregando não tiver dados, retorna vazio
        if not cls._data_cache:
            return SearchResponse(reply="Ainda não temos restaurantes cadastrados.", intent="empty", results=[])

        model = cls.get_model()

        # 0. Comandos Exatos (Atalho)
        comandos_exatos = ["ver todos", "ver tudo", "listar", "all", "restaurantes"]
        if user_query.lower() in comandos_exatos:
            return SearchResponse(reply="Aqui estão todas as opções:", intent="show_all", results=cls._data_cache)

        # 1. Roteamento de Intenção (O usuário quer buscar ou ver tudo?)
        user_embedding = model.encode(user_query, convert_to_tensor=True)

        # Comparação segura de intenção
        if cls._intent_embeddings is not None:
            intent_scores = util.cos_sim(user_embedding, cls._intent_embeddings)[0]
            # Se a intenção 0 (Show All) for maior que a 1 (Search)
            if intent_scores[0] > intent_scores[1] and intent_scores[0] > 0.5:
                return SearchResponse(reply="Listando tudo:", intent="show_all", results=cls._data_cache)

        # 2. Busca Ponderada (O "Coração" da busca)
        scores_name = util.cos_sim(user_embedding, cls._embeddings_names)[0]
        scores_category = util.cos_sim(user_embedding, cls._embeddings_categories)[0]
        scores_menu = util.cos_sim(user_embedding, cls._embeddings_menus)[0]

        final_scores = []
        for i in range(len(cls._data_cache)):
            s_name = scores_name[i].item()
            s_cat = scores_category[i].item()
            s_menu = scores_menu[i].item()

            # PESOS:
            # Nome do Restaurante vale muito (2.0)
            # Categoria vale médio (1.5) -> ex: "Italiana"
            # Item do Menu vale (1.2) -> ex: "Quero comer Lasanha" (agora o produto importa!)
            weighted_score = (s_name * 2.0) + (s_cat * 1.5) + (s_menu * 1.2)
            weighted_score = weighted_score / 4.7  # Normalização básica

            final_scores.append((weighted_score, cls._data_cache[i]))

        # Ordena do maior score para o menor
        final_scores.sort(key=lambda x: x[0], reverse=True)

        # Filtra apenas resultados relevantes (score > 0.25)
        good_matches = [item[1] for item in final_scores if item[0] > 0.25]

        if good_matches:
            top_match = good_matches[0]
            return SearchResponse(
                reply=f"Encontrei {top_match.name} e outras opções para você.",
                intent="search_result",
                results=good_matches
            )
        else:
            # Fallback: Se não achar nada parecido, mostra os top 3 gerais ou aleatórios
            fallback = cls._data_cache[:3]
            return SearchResponse(
                reply="Não encontrei exatamente isso, mas veja estas opções populares:",
                intent="no_match",
                results=fallback
            )