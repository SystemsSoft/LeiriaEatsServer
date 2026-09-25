"""
Teste do ciclo de vida da campanha conforme o agente a usa (conectaai/repositories/campaign_repo.py).

Antes, uma campanha criada por IA ficava em "draft" para sempre, mesmo com os agentes negociando e com
acordos aprovados — a lista de campanhas (abas Ativas / Em negociação / Rascunhos) nunca refletia o uso.
Agora: primeira negociação iniciada -> "negotiating"; acordo aprovado pelos dois lados -> "active".
Os avanços nunca regridem uma campanha que já está adiante (ex.: concluída).

Roda contra um SQLite em memória, sem rede.

Execução (na raiz do repo do servidor):
    python3 tests/test_conectaai_campaign_lifecycle.py
"""
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

from conectaai.models import sql_models  # noqa: E402,F401
from conectaai.repositories.campaign_repo import CampaignRepository  # noqa: E402

dbmod.Base.metadata.create_all(_engine)


def _campanha(db, **dados):
    base = {"name": "Campanha teste", "budget": 1000, "status": "draft", "description": "", "content_types": ["Reel"]}
    base.update(dados)
    return CampaignRepository.create(db, "emp-1", base, [])


def teste_primeira_negociacao_tira_a_campanha_de_rascunho():
    db = dbmod.SessionLocal()
    campanha = _campanha(db)
    assert campanha.status == "draft" and campanha.current_stage == "briefing"
    CampaignRepository.mark_negotiation_started(db, campanha.id)
    db.expire_all()
    campanha = CampaignRepository.get_by_id(db, campanha.id)
    assert campanha.status == "negotiating" and campanha.current_stage == "negotiation"
    print("OK  - negociação iniciada: rascunho -> em negociação (etapa Negociação)")


def teste_acordo_aprovado_ativa_a_campanha():
    db = dbmod.SessionLocal()
    for inicial in ("draft", "negotiating"):
        campanha = _campanha(db, status=inicial)
        CampaignRepository.mark_agreement_approved(db, campanha.id)
        db.expire_all()
        campanha = CampaignRepository.get_by_id(db, campanha.id)
        assert campanha.status == "active" and campanha.current_stage == "contract", (inicial, campanha.status, campanha.current_stage)
    print("OK  - acordo aprovado pelos dois lados: campanha vira ativa (etapa Contrato)")


def teste_avancos_nao_regridem_campanha_adiantada():
    db = dbmod.SessionLocal()
    concluida = _campanha(db, status="completed")
    concluida.current_stage = "payment"
    db.commit()
    CampaignRepository.mark_negotiation_started(db, concluida.id)
    CampaignRepository.mark_agreement_approved(db, concluida.id)
    ativa = _campanha(db, status="active")
    ativa.current_stage = "production"
    db.commit()
    CampaignRepository.mark_negotiation_started(db, ativa.id)
    db.expire_all()
    concluida = CampaignRepository.get_by_id(db, concluida.id)
    ativa = CampaignRepository.get_by_id(db, ativa.id)
    assert (concluida.status, concluida.current_stage) == ("completed", "payment")
    assert (ativa.status, ativa.current_stage) == ("active", "production")
    print("OK  - campanha concluída/em produção não regride quando outra negociação começa ou outro acordo fecha")


def teste_sem_campanha_nao_faz_nada():
    db = dbmod.SessionLocal()
    CampaignRepository.mark_negotiation_started(db, None)
    CampaignRepository.mark_negotiation_started(db, "id-que-nao-existe")
    CampaignRepository.mark_agreement_approved(db, None)
    print("OK  - negociação sem campanha (ou campanha inexistente) é ignorada sem erro")


if __name__ == "__main__":
    for nome, funcao in list(globals().items()):
        if nome.startswith("teste_") and callable(funcao):
            funcao()
    print("\nTodos os testes do ciclo de vida da campanha passaram.")
