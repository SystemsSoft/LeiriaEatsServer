"""
Teste de como uma campanha chega a "ativa" sem agentes (negociação 100% manual).

- Proposta enviada: tira a campanha de "rascunho" (-> em negociação); campanha de OUTRA empresa é recusada.
- Proposta aceita pelo creator: vincula o creator à campanha e ativa (etapa Contrato).
- Ativação manual (POST /campaigns/{id}/activate): exige ao menos um creator; não reativa concluída.
- Negociação: abrir duas vezes com o mesmo creator/campanha devolve a mesma mesa.

Roda contra um SQLite em memória, sem rede.

Execução (na raiz do repo do servidor):
    python3 tests/test_conectaai_campaign_activation.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CONECTAAI_GEMINI_API_KEY", "")

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import conectaai.core.database as dbmod

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
dbmod.engine = _engine
dbmod.SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

from conectaai.api.routes.agreement_routes import approve_agreement, reject_agreement  # noqa: E402
from conectaai.api.routes.campaign_routes import activate_campaign  # noqa: E402
from conectaai.api.routes.negotiation_routes import accept_current_offer, create_negotiation, send_counter_proposal  # noqa: E402
from conectaai.api.routes.proposal_routes import create_proposal, update_proposal  # noqa: E402
from conectaai.core.security import CurrentUser  # noqa: E402
from conectaai.models import sql_models  # noqa: E402
from conectaai.repositories.campaign_repo import CampaignRepository  # noqa: E402
from conectaai.repositories.mandate_repo import MandateRepository  # noqa: E402
from conectaai.schemas.agreement import RejectAgreementRequest  # noqa: E402
from conectaai.schemas.negotiation import CounterProposalRequest, StartNegotiationRequest  # noqa: E402
from conectaai.schemas.proposal import ProposalCreateRequest, ProposalUpdateRequest  # noqa: E402

dbmod.Base.metadata.create_all(_engine)

_n = 0


def _usuario(db, role):
    global _n
    _n += 1
    user = sql_models.UserDB(email=f"{role}{_n}@teste.com", password_hash="x", role=role, name=f"{role} {_n}")
    db.add(user)
    db.flush()
    perfil = (sql_models.CompanyDB if role == "company" else sql_models.CreatorDB)(user_id=user.id, name=f"{role} {_n}")
    db.add(perfil)
    db.commit()
    return CurrentUser(user.id, role), perfil


def _campanha(db, empresa, **dados):
    base = {"name": "Verão", "budget": 1000, "status": "draft", "description": "", "content_types": ["Reel"]}
    base.update(dados)
    return CampaignRepository.create(db, empresa.id, base, [])


def _recarregar(db, campanha):
    db.expire_all()
    return CampaignRepository.get_by_id(db, campanha.id)


def _erro(fn):
    try:
        fn()
    except HTTPException as e:
        return e
    raise AssertionError("deveria ter falhado")


def teste_proposta_enviada_tira_a_campanha_de_rascunho():
    db = dbmod.SessionLocal()
    user, empresa = _usuario(db, "company")
    _, creator = _usuario(db, "creator")
    campanha = _campanha(db, empresa)
    create_proposal(ProposalCreateRequest(creator_id=creator.id, campaign_id=campanha.id), current_user=user, db=db)
    assert _recarregar(db, campanha).status == "negotiating"
    print("OK  - proposta enviada: rascunho -> em negociação")


def teste_proposta_com_campanha_de_outra_empresa_e_recusada():
    db = dbmod.SessionLocal()
    user, _ = _usuario(db, "company")
    _, outra = _usuario(db, "company")
    _, creator = _usuario(db, "creator")
    campanha_alheia = _campanha(db, outra)
    e = _erro(lambda: create_proposal(ProposalCreateRequest(creator_id=creator.id, campaign_id=campanha_alheia.id), current_user=user, db=db))
    assert e.status_code == 400
    assert _recarregar(db, campanha_alheia).status == "draft"
    print("OK  - campanha de outra empresa: 400, nada muda")


def teste_proposta_aceita_vincula_o_creator_e_ativa():
    db = dbmod.SessionLocal()
    user, empresa = _usuario(db, "company")
    creator_user, creator = _usuario(db, "creator")
    campanha = _campanha(db, empresa)
    proposta = create_proposal(ProposalCreateRequest(creator_id=creator.id, campaign_id=campanha.id), current_user=user, db=db)
    update_proposal(proposta.id, ProposalUpdateRequest(status="accepted"), current_user=creator_user, db=db)
    campanha = _recarregar(db, campanha)
    assert campanha.status == "active" and campanha.current_stage == "contract"
    assert CampaignRepository.creator_ids_of(campanha) == [creator.id]
    print("OK  - proposta aceita: creator vinculado e campanha ativa (etapa Contrato)")


def teste_proposta_recusada_nao_ativa():
    db = dbmod.SessionLocal()
    user, empresa = _usuario(db, "company")
    creator_user, creator = _usuario(db, "creator")
    campanha = _campanha(db, empresa)
    proposta = create_proposal(ProposalCreateRequest(creator_id=creator.id, campaign_id=campanha.id), current_user=user, db=db)
    update_proposal(proposta.id, ProposalUpdateRequest(status="rejected"), current_user=creator_user, db=db)
    campanha = _recarregar(db, campanha)
    assert campanha.status == "negotiating" and CampaignRepository.creator_ids_of(campanha) == []
    print("OK  - proposta recusada: campanha não ativa e creator não vinculado")


def teste_proposta_aceita_de_campanha_concluida_nao_regride():
    db = dbmod.SessionLocal()
    user, empresa = _usuario(db, "company")
    creator_user, creator = _usuario(db, "creator")
    campanha = _campanha(db, empresa, status="completed", current_stage="payment")
    proposta = create_proposal(ProposalCreateRequest(creator_id=creator.id, campaign_id=campanha.id), current_user=user, db=db)
    update_proposal(proposta.id, ProposalUpdateRequest(status="accepted"), current_user=creator_user, db=db)
    campanha = _recarregar(db, campanha)
    assert campanha.status == "completed" and campanha.current_stage == "payment"
    print("OK  - campanha concluída não regride por proposta aceita")


def teste_ativacao_manual_exige_um_creator():
    db = dbmod.SessionLocal()
    user, empresa = _usuario(db, "company")
    _, creator = _usuario(db, "creator")
    campanha = _campanha(db, empresa)
    e = _erro(lambda: activate_campaign(campanha.id, current_user=user, db=db))
    assert e.status_code == 400 and "influenciador" in e.detail
    CampaignRepository.link_creator(db, campanha.id, creator.id)
    resposta = activate_campaign(campanha.id, current_user=user, db=db)
    assert resposta.status == "active" and resposta.current_stage == "contract"
    assert activate_campaign(campanha.id, current_user=user, db=db).status == "active"  # idempotente
    print("OK  - ativação manual: exige creator, ativa e é idempotente")


def teste_ativacao_manual_nao_reativa_concluida_nem_campanha_alheia():
    db = dbmod.SessionLocal()
    user, empresa = _usuario(db, "company")
    _, outra = _usuario(db, "company")
    _, creator = _usuario(db, "creator")
    concluida = _campanha(db, empresa, status="completed")
    CampaignRepository.link_creator(db, concluida.id, creator.id)
    assert _erro(lambda: activate_campaign(concluida.id, current_user=user, db=db)).status_code == 400
    assert _recarregar(db, concluida).status == "completed"
    alheia = _campanha(db, outra)
    assert _erro(lambda: activate_campaign(alheia.id, current_user=user, db=db)).status_code == 404
    print("OK  - concluída não reativa; campanha de outra empresa dá 404")


def _mesa(db):
    """Empresa, creator, campanha e a negociação já aberta entre eles."""
    user, empresa = _usuario(db, "company")
    creator_user, creator = _usuario(db, "creator")
    campanha = _campanha(db, empresa)
    mandato = MandateRepository.create(
        db,
        owner_type="company",
        owner_id=empresa.id,
        data={"campaign_id": campanha.id, "objective": "Divulgar", "target_count": 1, "budget_total": 1000, "ideal_price": 500,
              "price_floor": 0, "price_ceiling": 600, "auto_approve_limit": 0, "currency": "BRL", "max_rounds": 4,
              "deliverables": [], "negotiable_fields": [], "non_negotiable_fields": [], "deadline_earliest": None,
              "deadline_latest": None, "exclusivity_allowed": False, "extra_terms": {}, "expires_at": None},
    )
    pedido = StartNegotiationRequest(creator_id=creator.id, campaign_id=campanha.id, company_mandate_id=mandato.id)
    return user, creator_user, empresa, creator, campanha, pedido


def teste_negociacao_aberta_duas_vezes_devolve_a_mesma_mesa():
    db = dbmod.SessionLocal()
    user, _, _, _, campanha, pedido = _mesa(db)
    primeira = create_negotiation(pedido, current_user=user, db=db)
    segunda = create_negotiation(pedido, current_user=user, db=db)
    assert primeira.id == segunda.id
    assert _recarregar(db, campanha).status == "negotiating"
    print("OK  - abrir a negociação duas vezes não cria duas mesas")


def teste_fluxo_manual_completo_ate_a_campanha_ativa():
    db = dbmod.SessionLocal()
    user, creator_user, _, creator, campanha, pedido = _mesa(db)
    negociacao = create_negotiation(pedido, current_user=user, db=db)

    send_counter_proposal(negociacao.id, CounterProposalRequest(price=450, deadline_days=15, text="Topa?"), current_user=creator_user, db=db)
    aceita = accept_current_offer(negociacao.id, current_user=user, db=db)
    assert aceita.state == "waiting_approval" and aceita.agreement_id

    approve_agreement(aceita.agreement_id, current_user=user, db=db)
    assert _recarregar(db, campanha).status == "negotiating", "um lado só aprovou: ainda não ativa"
    approve_agreement(aceita.agreement_id, current_user=creator_user, db=db)

    campanha = _recarregar(db, campanha)
    assert campanha.status == "active" and CampaignRepository.creator_ids_of(campanha) == [creator.id]
    db.expire_all()
    mesa = db.query(sql_models.NegotiationDB).filter_by(id=negociacao.id).one()
    assert mesa.state == "agreed" and mesa.outcome == "agreed"
    # Fechada: uma nova mesa para o mesmo creator/campanha pode ser aberta.
    assert create_negotiation(pedido, current_user=user, db=db).id != negociacao.id
    print("OK  - contraproposta -> aceite -> aprovação dos dois: campanha ativa e negociação encerrada")


def teste_acordo_recusado_reabre_a_mesa():
    db = dbmod.SessionLocal()
    user, creator_user, _, _, campanha, pedido = _mesa(db)
    negociacao = create_negotiation(pedido, current_user=user, db=db)
    send_counter_proposal(negociacao.id, CounterProposalRequest(price=450), current_user=creator_user, db=db)
    aceita = accept_current_offer(negociacao.id, current_user=user, db=db)

    reject_agreement(aceita.agreement_id, RejectAgreementRequest(reason="Valor baixo"), current_user=creator_user, db=db)
    db.expire_all()
    mesa = db.query(sql_models.NegotiationDB).filter_by(id=negociacao.id).one()
    assert mesa.state == "waiting_human_creator"
    nova = send_counter_proposal(negociacao.id, CounterProposalRequest(price=600), current_user=creator_user, db=db)
    assert nova.current_offer["price"] == 600
    assert _recarregar(db, campanha).status == "negotiating"
    print("OK  - acordo recusado: a mesa reabre e aceita nova contraproposta")


if __name__ == "__main__":
    for nome, fn in list(globals().items()):
        if nome.startswith("teste_"):
            fn()
    print("\nTodos os testes de ativação da campanha passaram.")
