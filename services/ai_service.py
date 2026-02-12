# Arquivo: services/ai_service.py
from sentence_transformers import SentenceTransformer, util
from repositories.restaurant_repo import RestaurantRepository
from schemas.models import SearchResponse, Restaurant
from sqlalchemy.orm import Session
import torch


class AIService:
    _model = None

    # Índices Globais (Restaurantes)
    _embeddings_names = None
    _embeddings_categories = None

    # Índice Granular (Produtos Individuais)
    _embeddings_products = None
    _product_owner_map = []  # Lista para saber de qual restaurante é cada produto
    _product_data_cache = []  # Lista com os nomes dos produtos para mostrar qual foi achado

    _intent_embeddings = None
    _data_cache = None

    INTENT_SHOW_ALL = "Mostrar a lista com todos os restaurantes e opções disponíveis"
    INTENT_SEARCH = "Gostaria de buscar uma comida ou prato específico"

    @classmethod
    def get_model(cls):
        if cls._model is None:
            print("⏳ AI: Carregando modelo SentenceTransformer...")
            cls._model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
            cls._intent_embeddings = cls._model.encode([cls.INTENT_SHOW_ALL, cls.INTENT_SEARCH], convert_to_tensor=True)
        return cls._model

    @classmethod
    def reload_data(cls, db: Session):
        print("🔄 AI: Atualizando índice de busca com dados do banco...")
        data = RestaurantRepository.get_all(db)

        if not data:
            cls._data_cache = []
            return

        cls._data_cache = data
        cls._index_data(data)
        print(f"✅ AI: Índice atualizado com {len(data)} restaurantes e {len(cls._product_owner_map)} produtos.")

    @classmethod
    def _index_data(cls, restaurants: list[Restaurant]):
        model = cls.get_model()

        # 1. Indexação Nível Restaurante (Nome e Categoria)
        names_list = [r.name for r in restaurants]
        categories_list = [r.category for r in restaurants]

        cls._embeddings_names = model.encode(names_list, convert_to_tensor=True)
        cls._embeddings_categories = model.encode(categories_list, convert_to_tensor=True)

        # 2. Indexação Nível PRODUTO (A Grande Mudança)
        # Em vez de juntar tudo, criamos uma lista gigante com TODOS os produtos de TODOS os restaurantes
        product_texts = []
        cls._product_owner_map = []  # Guarda o índice do restaurante dono do produto
        cls._product_data_cache = []  # Guarda o nome do produto

        for r_index, r in enumerate(restaurants):
            if not r.products:
                continue

            for p in r.products:
                # Texto rico para busca: "X-Bacon (Pão, carne, queijo e bacon)"
                text = f"{p.name} ({p.description})"
                product_texts.append(text)

                # Mapeia: O produto da posição X pertence ao restaurante r_index
                cls._product_owner_map.append(r_index)
                cls._product_data_cache.append(p.name)

        if product_texts:
            cls._embeddings_products = model.encode(product_texts, convert_to_tensor=True)
        else:
            cls._embeddings_products = None

    @classmethod
    def process_search(cls, user_query: str, db: Session) -> SearchResponse:
        if cls._data_cache is None:
            cls.reload_data(db)

        if not cls._data_cache:
            return SearchResponse(reply="Sem dados.", intent="empty", results=[])

        model = cls.get_model()

        # 0. Atalhos
        if user_query.lower() in ["ver todos", "tudo", "restaurantes"]:
            return SearchResponse(reply="Aqui estão todas as opções:", intent="show_all", results=cls._data_cache)

        # 1. Intenção
        user_embedding = model.encode(user_query, convert_to_tensor=True)
        if cls._intent_embeddings is not None:
            intent_scores = util.cos_sim(user_embedding, cls._intent_embeddings)[0]
            if intent_scores[0] > intent_scores[1] and intent_scores[0] > 0.5:
                return SearchResponse(reply="Listando tudo:", intent="show_all", results=cls._data_cache)

        # 2. Busca Híbrida Otimizada

        # A. Scores dos Restaurantes (Nome e Categoria)
        scores_name = util.cos_sim(user_embedding, cls._embeddings_names)[0]
        scores_category = util.cos_sim(user_embedding, cls._embeddings_categories)[0]

        # B. Score dos PRODUTOS (Busca Profunda)
        # Cria uma lista de "Melhor Nota de Produto" para cada restaurante, iniciando em 0
        best_product_scores = [0.0] * len(cls._data_cache)
        best_product_names = [""] * len(cls._data_cache)  # Para saber qual prato ganhou

        if cls._embeddings_products is not None:
            # Compara a query contra TODOS os produtos de uma vez
            all_product_scores = util.cos_sim(user_embedding, cls._embeddings_products)[0]

            # Agora varremos os scores dos produtos e atribuímos ao dono
            for i, score in enumerate(all_product_scores):
                r_idx = cls._product_owner_map[i]  # De quem é esse produto?
                val = score.item()

                # Se esse produto tem uma nota maior que a nota atual do restaurante, atualiza
                if val > best_product_scores[r_idx]:
                    best_product_scores[r_idx] = val
                    best_product_names[r_idx] = cls._product_data_cache[i]  # Guarda o nome do prato campeão

        # 3. Cálculo Final
        final_results = []
        for i in range(len(cls._data_cache)):
            s_name = scores_name[i].item()
            s_cat = scores_category[i].item()
            s_prod = best_product_scores[i]  # A nota do MELHOR prato que esse restaurante tem

            # Pesos Ajustados: O Produto agora tem muito peso
            # Se o usuário digita "Hamburguer", e o restaurante chama "Sushi House" mas tem um hamburguer, ele deve aparecer.
            weighted_score = (s_name + s_cat + s_prod) / 3.0

            if weighted_score > 0.28:  # Limiar de corte
                final_results.append({
                    "score": weighted_score,
                    "restaurant": cls._data_cache[i],
                    "highlight": best_product_names[i] if s_prod > 0.4 else None
                    # Só destaca se o produto for relevante mesmo
                })

        # Ordena
        final_results.sort(key=lambda x: x["score"], reverse=True)

        # Limpa para retorno
        clean_results = [item["restaurant"] for item in final_results]

        if clean_results:
            top_item = final_results[0]
            # Resposta dinâmica: Se achou por causa de um prato, cita o prato
            if top_item["highlight"]:
                reply = f"Encontrei '{top_item['highlight']}' no {top_item['restaurant'].name} e outras opções."
            else:
                reply = f"Encontrei {top_item['restaurant'].name} e similares."

            return SearchResponse(reply=reply, intent="search_result", results=clean_results)

        else:
            return SearchResponse(reply="Não encontrei nada específico, mas veja estes:", intent="no_match",
                                  results=cls._data_cache[:3])