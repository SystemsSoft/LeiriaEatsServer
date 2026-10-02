"""
Teste da busca de creators com avaliação pela IA (POST /ai/match/creators).

A busca semântica só pré-seleciona candidatos; o Gemini recebe os dados do perfil (seguidores, preço,
engajamento…) e devolve nota + motivo por creator. Aqui o Gemini e os embeddings são substituídos por
fakes — confere que o prompt leva os dados reais, que a resposta da IA vira o ranking (ignorando refs
inventados/repetidos e clampando a nota) e que, se a IA falhar, a busca continua funcionando sem ela.

Roda contra um SQLite em memória, sem rede.

Execução (na raiz do repo do servidor):
    python3 tests/test_conectaai_creator_match_ai.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CONECTAAI_GEMINI_API_KEY", "")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import conectaai.core.database as dbmod

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
dbmod.engine = _engine
dbmod.SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

from conectaai.api.routes.match_routes import match_creators  # noqa: E402
from conectaai.core.config import settings  # noqa: E402
from conectaai.core.security import CurrentUser  # noqa: E402
from conectaai.models import sql_models  # noqa: E402
from conectaai.schemas.ai import MatchCreatorsRequest  # noqa: E402
from conectaai.schemas.ai_structured import CreatorRerankItem, CreatorRerankResult  # noqa: E402
from conectaai.services.ai import embeddings, gemini_client  # noqa: E402

dbmod.Base.metadata.create_all(_engine)

# Embeddings sempre indisponíveis nos testes: a pré-seleção cai na palavra-chave, sem rede.
embeddings.embed_texts = lambda texts, task_type="RETRIEVAL_DOCUMENT": None

_CREATORS = [
    {"name": "Ana Grande", "followers": 900_000, "price_min": 5000, "price_max": 8000, "engagement_rate": 2.1},
    {"name": "Bia Media", "followers": 120_000, "price_min": 800, "price_max": 1500, "engagement_rate": 4.0},
    {"name": "Carla Micro", "followers": 15_000, "price_min": 150, "price_max": 300, "engagement_rate": 7.5},
]


def _cenario():
    db = dbmod.SessionLocal()
    for model in (sql_models.AiCallLogDB, sql_models.CreatorDB, sql_models.CompanyDB, sql_models.UserDB):
        db.query(model).delete()
    db.commit()
    empresa = sql_models.UserDB(email="empresa@teste.com", password_hash="x", role="company", name="Empresa")
    db.add(empresa)
    db.flush()
    db.add(sql_models.CompanyDB(user_id=empresa.id, name="Empresa"))
    for i, dados in enumerate(_CREATORS):
        user = sql_models.UserDB(email=f"c{i}@teste.com", password_hash="x", role="creator", name=dados["name"])
        db.add(user)
        db.flush()
        db.add(sql_models.CreatorDB(user_id=user.id, city="São Paulo", categories=["beleza"], platforms=[{"platform": "Instagram", "url": ""}], **dados))
    db.commit()
    return db, CurrentUser(empresa.id, "company")


def _candidatos(user_content: str) -> dict:
    """{nome: perfil} a partir do JSON de candidatos que foi no prompt."""
    bloco = user_content.split("Creators candidatos:\n", 1)[1].split("\n\n", 1)[0]
    return {p["nome"]: p for p in json.loads(bloco)}


def _fake_gemini(respond):
    """Substitui gemini_client.generate_json; `respond(user_content)` devolve o CreatorRerankResult (ou None = falha)."""
    chamadas = []

    def fake(**kwargs):
        chamadas.append(kwargs)
        parsed = respond(kwargs["user_content"])
        status = "ok" if parsed is not None else "invalid_json"
        return gemini_client.GeminiCallResult(parsed, "{}", 0, 1, status, 10)

    gemini_client.generate_json = fake
    settings.GEMINI_API_KEYS = ["fake"]
    return chamadas


def _buscar(db, usuario, texto, **extra):
    return match_creators(MatchCreatorsRequest(text=texto, limit=10, **extra), current_user=usuario, db=db)


def teste_ia_ordena_pelo_pedido_e_escreve_motivo_com_dados_do_perfil():
    db, usuario = _cenario()

    def responder(user_content):
        refs = {nome: p["ref"] for nome, p in _candidatos(user_content).items()}
        return CreatorRerankResult(
            summary="Priorizei creators de beleza com mais seguidores.",
            results=[
                CreatorRerankItem(ref=refs["Bia Media"], score=70, reason="Tem 120 mil seguidores."),
                CreatorRerankItem(ref=refs["Ana Grande"], score=95, reason="Tem 900 mil seguidores, o maior alcance."),
                CreatorRerankItem(ref="c99", score=99, reason="ref inventado"),
                CreatorRerankItem(ref=refs["Ana Grande"], score=10, reason="repetido"),
            ],
        )

    chamadas = _fake_gemini(responder)
    resposta = _buscar(db, usuario, "creators de beleza com mais seguidores")

    assert resposta.source == "ai", resposta.source
    assert resposta.summary == "Priorizei creators de beleza com mais seguidores."
    assert [r.creator.name for r in resposta.results] == ["Ana Grande", "Bia Media"], [r.creator.name for r in resposta.results]
    assert resposta.results[0].match_score == 95
    assert resposta.results[0].match_reason == "Tem 900 mil seguidores, o maior alcance."
    assert resposta.results[0].creator.match_reason == resposta.results[0].match_reason
    assert resposta.total_found == 2

    perfil = _candidatos(chamadas[0]["user_content"])["Carla Micro"]
    assert perfil["seguidores"] == 15_000 and perfil["preco_min"] == 150 and perfil["engajamento_pct"] == 7.5, perfil
    assert perfil["plataformas"] == ["Instagram"], perfil
    assert "id" not in perfil  # o modelo só vê o apelido curto, nunca o id real
    assert db.query(sql_models.AiCallLogDB).filter_by(purpose="creator_match").count() == 1
    print("OK  - IA ordena pelo pedido, motivo vem da IA; refs inventados/repetidos são ignorados")


def teste_nota_fora_da_faixa_e_motivo_longo_sao_corrigidos():
    db, usuario = _cenario()

    def responder(user_content):
        refs = {nome: p["ref"] for nome, p in _candidatos(user_content).items()}
        return CreatorRerankResult(results=[
            CreatorRerankItem(ref=refs["Carla Micro"], score=180, reason="x " * 400),
            CreatorRerankItem(ref=refs["Bia Media"], score=-5, reason=""),
        ])

    _fake_gemini(responder)
    resposta = _buscar(db, usuario, "mais barato")
    assert [r.match_score for r in resposta.results] == [100, 0], [r.match_score for r in resposta.results]
    assert len(resposta.results[0].match_reason) <= 300
    assert resposta.results[1].match_reason  # motivo vazio vira um texto padrão, nunca string vazia
    print("OK  - nota é limitada a 0-100 e motivo longo/vazio é tratado")


def teste_pedidos_anteriores_vao_para_a_ia():
    db, usuario = _cenario()
    chamadas = _fake_gemini(lambda _: CreatorRerankResult(summary="", results=[]))
    resposta = _buscar(db, usuario, "agora só os mais baratos", previous_queries=["creators de beleza em São Paulo"])
    prompt = chamadas[0]["user_content"]
    assert "creators de beleza em São Paulo" in prompt and "agora só os mais baratos" in prompt
    # Lista vazia é resposta válida da IA (ninguém atende), não falha.
    assert resposta.source == "ai" and resposta.results == []
    print("OK  - pedidos anteriores da conversa entram no prompt (refinamento)")


def teste_falha_da_ia_mantem_a_busca_sem_ia():
    db, usuario = _cenario()
    _fake_gemini(lambda _: None)  # JSON inválido / erro
    resposta = _buscar(db, usuario, "beleza em São Paulo")
    assert resposta.source == "heuristic", resposta.source
    assert len(resposta.results) == 3
    assert "semântica indisponível" in resposta.results[0].match_reason
    print("OK  - IA falhou: devolve o ranking por palavra-chave, sem erro")


def teste_sem_chave_nem_chama_a_ia():
    db, usuario = _cenario()
    chamadas = _fake_gemini(lambda _: CreatorRerankResult())
    settings.GEMINI_API_KEYS = []
    resposta = _buscar(db, usuario, "beleza")
    assert chamadas == [] and resposta.source == "heuristic"
    print("OK  - sem chave configurada: nenhuma chamada ao Gemini")


if __name__ == "__main__":
    teste_ia_ordena_pelo_pedido_e_escreve_motivo_com_dados_do_perfil()
    teste_nota_fora_da_faixa_e_motivo_longo_sao_corrigidos()
    teste_pedidos_anteriores_vao_para_a_ia()
    teste_falha_da_ia_mantem_a_busca_sem_ia()
    teste_sem_chave_nem_chama_a_ia()
