"""
Testes da busca híbrida léxica + vetorial (F5.2 do PLANO_EXECUCAO_IA.md).

O banco real deste projeto é MySQL (mysql+pymysql em core/database.py), não Postgres —
por isso o lado léxico roda em Python sobre o cache já carregado (AIService._score_lexical),
em vez de depender de um recurso específico de dialeto como pg_trgm ou FULLTEXT do MySQL
(que exigiria migração e não pôde ser validada contra o banco real nesta sessão).

Execução:
    python3 tests/test_hybrid_search.py
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


def _montar_db(produtos_spec):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    r = RestaurantDB(name="Rest Teste", category="Variado", login="t", password="x", gid="01REST1")
    db.add(r)
    db.commit()
    db.refresh(r)

    produtos = [
        ProductDB(gid=gid, name=name, description="", search_tags=tags or "",
                   price=10.0, category="X", restaurant_id=r.id, is_available=True)
        for gid, name, tags in produtos_spec
    ]
    db.add_all(produtos)
    db.commit()
    return db


# ── 1. Unidade: _score_lexical e _rrf isolados ──────────────────────────────

class _ProdutoFake:
    def __init__(self, name, search_tags=""):
        self.name = name
        self.search_tags = search_tags


def teste_score_lexical_match_completo_da_score_maximo():
    p = _ProdutoFake(name="Francesinha Especial")
    query = AIService._normalizar_texto_busca("francesinha")
    assert AIService._score_lexical(query, p) == 1.0
    print("OK  - substring completo da query no nome do produto dá score 1.0")


def teste_score_lexical_ignora_acentos_e_maiusculas():
    p = _ProdutoFake(name="Açaí na Tigela")
    query = AIService._normalizar_texto_busca("ACAI")
    assert AIService._score_lexical(query, p) > 0
    print("OK  - comparação léxica ignora acentuação e caixa")


def teste_score_lexical_ignora_stopwords_genericas():
    p = _ProdutoFake(name="Pizza Margherita")
    query = AIService._normalizar_texto_busca("quero um produto qualquer")
    # "quero", "produto" são stopwords; "qualquer" não aparece no nome -> score 0
    assert AIService._score_lexical(query, p) == 0.0
    print("OK  - palavras genéricas de pedido (quero, produto) não geram falso positivo léxico")


def teste_rrf_favorece_item_bem_ranqueado_nas_duas_listas():
    score_ambas_top = AIService._rrf(0, 0)
    score_so_vetorial = AIService._rrf(0, None)
    score_so_lexical = AIService._rrf(None, 0)
    assert score_ambas_top > score_so_vetorial
    assert score_ambas_top > score_so_lexical
    assert score_so_vetorial == score_so_lexical  # rank 0 em uma lista só, mesma magnitude
    print("OK  - RRF dá score maior a item bem ranqueado nas duas listas do que em só uma")


# ── 2. Integração: nome próprio de prato é resgatado mesmo com score vetorial mediano ──

def teste_busca_hibrida_resgata_nome_proprio_com_score_vetorial_mediano():
    """O caso central do F5.2: o embedding "erra" (score mediano) num nome de prato
    regional, mas a query bate exatamente com o nome — a fusão precisa trazer esse
    produto para o resultado mesmo que ele não passasse no corte vetorial puro."""
    db = _montar_db([
        ("01FRANC", "Francesinha", ""),       # nome próprio - vamos simular score vetorial baixo
        ("01GENER", "Prato do Dia", ""),       # concorrente com score vetorial alto
        ("01OUTRO", "Sobremesa Qualquer", ""),  # irrelevante nos dois eixos
    ])

    class _ModeloFake:
        def encode(self, texto_ou_lista, convert_to_tensor=True):
            n = len(texto_ou_lista) if isinstance(texto_ou_lista, list) else 1
            return torch.zeros((n, 4))

    AIService.get_model = classmethod(lambda cls: _ModeloFake())
    AIService.reload_data(db)

    ordem_gids = [p.gid for p in AIService._product_obj_cache]
    # "Francesinha" tem score vetorial mediano (0.55 -> abaixo do corte relativo de 97%
    # sobre o melhor, que é 0.85), "Prato do Dia" domina o eixo vetorial.
    gid_para_score_vetorial = {"01FRANC": 0.55, "01GENER": 0.85, "01OUTRO": 0.40}
    scores_na_ordem = [gid_para_score_vetorial[g] for g in ordem_gids]

    def fake_cos_sim(query_emb, corpus_emb):
        n = corpus_emb.shape[0]
        if n == len(scores_na_ordem):
            return torch.tensor([scores_na_ordem])
        return torch.zeros((1, n))

    original_cos_sim = ai_service_module.util.cos_sim
    ai_service_module.util.cos_sim = fake_cos_sim
    try:
        resultado = AIService.process_search(user_query="quero uma francesinha", db=db, scope="product")
    finally:
        ai_service_module.util.cos_sim = original_cos_sim

    gids_retornados = {p.gid for p in resultado.productResults}
    assert "01FRANC" in gids_retornados, (
        "Francesinha (match léxico exato, score vetorial 0.55) deveria ser resgatada "
        "pela busca híbrida mesmo abaixo do corte vetorial relativo"
    )
    assert "01OUTRO" not in gids_retornados, (
        "produto sem sinal vetorial nem léxico não deveria aparecer"
    )
    print("OK  - busca híbrida resgata nome próprio de prato com score vetorial mediano "
          "graças ao match léxico, sem depender só do cosseno")


if __name__ == "__main__":
    teste_score_lexical_match_completo_da_score_maximo()
    teste_score_lexical_ignora_acentos_e_maiusculas()
    teste_score_lexical_ignora_stopwords_genericas()
    teste_rrf_favorece_item_bem_ranqueado_nas_duas_listas()
    teste_busca_hibrida_resgata_nome_proprio_com_score_vetorial_mediano()
    print("\nTodos os testes de busca híbrida passaram.")
