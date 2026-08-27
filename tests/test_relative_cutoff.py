"""
Teste do corte relativo de score (F5.1 do PLANO_EXECUCAO_IA.md).

Com E5, o cosseno fica comprimido numa faixa alta — o threshold fixo 0.45 na prática não
filtrava quase nada, e o corte real sempre foi o [:6]. Este teste prova que um candidato
que passaria no threshold fixo antigo (score > 0.45) agora é descartado quando está muito
abaixo do melhor resultado da busca (score < 97% do top-1).

Roda process_search contra um SQLite em memória (fixtures de tests/test_pipeline_integration.py)
com util.cos_sim monkeypatchado para devolver scores controlados — não depende do modelo E5
real nem da qualidade semântica de fato, só da lógica de corte.

Execução:
    python3 tests/test_relative_cutoff.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

import torch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base
from core.sql_models import RestaurantDB, ProductDB
from services import ai_service as ai_service_module
from services.ai_service import AIService


def _montar_db_com_tres_produtos():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    r = RestaurantDB(name="Rest Teste", category="Variado", login="t", password="x", gid="01REST1")
    db.add(r)
    db.commit()
    db.refresh(r)

    produtos = [
        ProductDB(gid="01TOP", name="Produto Top", description="", price=10.0, category="X", restaurant_id=r.id, is_available=True),
        ProductDB(gid="01NEAR", name="Produto Próximo", description="", price=10.0, category="X", restaurant_id=r.id, is_available=True),
        ProductDB(gid="01FAR", name="Produto Distante", description="", price=10.0, category="X", restaurant_id=r.id, is_available=True),
    ]
    db.add_all(produtos)
    db.commit()
    return db


def teste_corte_relativo_descarta_candidato_fraco_mas_acima_do_piso_antigo():
    db = _montar_db_com_tres_produtos()

    class _ModeloFake:
        def encode(self, texto_ou_lista, convert_to_tensor=True):
            n = len(texto_ou_lista) if isinstance(texto_ou_lista, list) else 1
            return torch.zeros((n, 4))

    AIService.get_model = classmethod(lambda cls: _ModeloFake())
    AIService.reload_data(db)

    # Scores fixos: 0.90 (top), 0.88 (>= 0.90*0.97=0.873 -> deve sobreviver),
    # 0.60 (> 0.45, passaria no piso antigo, mas < 0.873 -> deve ser descartado agora).
    gid_para_score = {"01TOP": 0.90, "01NEAR": 0.88, "01FAR": 0.60}
    ordem_gids = [p.gid for p in AIService._product_obj_cache]
    scores_na_ordem = [gid_para_score[g] for g in ordem_gids]

    def fake_cos_sim(query_emb, corpus_emb):
        n = corpus_emb.shape[0]
        if n == len(scores_na_ordem):
            return torch.tensor([scores_na_ordem])
        # chamada de nomes/categorias de restaurante — score baixo, irrelevante ao teste
        return torch.zeros((1, n))

    original_cos_sim = ai_service_module.util.cos_sim
    ai_service_module.util.cos_sim = fake_cos_sim
    try:
        resultado = AIService.process_search(user_query="produto qualquer", db=db, scope="product")
    finally:
        ai_service_module.util.cos_sim = original_cos_sim

    gids_retornados = {p.gid for p in resultado.productResults}
    assert "01TOP" in gids_retornados, "produto de maior score deveria estar no resultado"
    assert "01NEAR" in gids_retornados, "produto próximo do top (88% do melhor) deveria sobreviver ao corte"
    assert "01FAR" not in gids_retornados, (
        "produto com score 0.60 (bem abaixo do top 0.90) deveria ser descartado pelo corte "
        "relativo, mesmo passando no antigo piso fixo de 0.45"
    )
    print("OK  - corte relativo mantém candidatos próximos do top-1 e descarta os muito abaixo, "
          "mesmo quando o piso fixo antigo (0.45) os teria deixado passar")


if __name__ == "__main__":
    teste_corte_relativo_descarta_candidato_fraco_mas_acima_do_piso_antigo()
    print("\nTeste de corte relativo passou.")
