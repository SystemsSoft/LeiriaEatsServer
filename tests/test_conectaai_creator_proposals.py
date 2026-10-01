"""
Teste das propostas que VÊM do creator para a empresa (candidatura a uma campanha).

- O creator envia (POST /proposals/from-creator): a empresa dona da campanha é notificada.
- Quem responde é sempre o OUTRO lado: candidatura do creator -> só a empresa dona; convite da
  empresa -> só o creator convidado. O próprio remetente não pode se auto-aprovar.
- Aceitar vincula o creator à campanha e ativa; recusar não. Quem enviou é notificado.
- Sem duplicata pendente, sem campanha concluída, sem quem já está na campanha.
- Só uma proposta pendente pode ser respondida.

Roda contra um SQLite em memória, sem rede.

Execução (na raiz do repo do servidor):
    python3 tests/test_conectaai_creator_proposals.py
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

from conectaai.api.routes.proposal_routes import (  # noqa: E402
    create_proposal,
    create_proposal_from_creator,
    list_proposals,
    update_proposal,
)
from conectaai.core.security import CurrentUser  # noqa: E402
from conectaai.models import sql_models  # noqa: E402
from conectaai.repositories.campaign_repo import CampaignRepository  # noqa: E402
from conectaai.schemas.proposal import (  # noqa: E402
    CreatorProposalCreateRequest,
    ProposalCreateRequest,
    ProposalUpdateRequest,
)

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
    base = {"name": "Verão", "budget": 1000, "status": "active", "description": "", "content_types": ["Reel"]}
    base.update(dados)
    return CampaignRepository.create(db, empresa.id, base, [])


def _erro(fn):
    try:
        fn()
    except HTTPException as e:
        return e
    raise AssertionError("deveria ter falhado")


def _avisos(db, perfil):
    return db.query(sql_models.NotificationDB).filter_by(user_id=perfil.user_id).all()


def _candidatar(db, creator_user, campanha, **extra):
    dados = {"campaign_id": campanha.id, "content_type": "Reel", "quantity": 2, "budget": 450, "message": "Topo!"}
    dados.update(extra)
    return create_proposal_from_creator(CreatorProposalCreateRequest(**dados), current_user=creator_user, db=db)


def teste_creator_envia_proposta_e_a_empresa_dona_e_notificada():
    db = dbmod.SessionLocal()
    _, empresa = _usuario(db, "company")
    creator_user, creator = _usuario(db, "creator")
    campanha = _campanha(db, empresa)
    p = _candidatar(db, creator_user, campanha)
    assert p.sender_role == "creator" and p.status == "pending"
    assert p.company_id == empresa.id and p.creator_id == creator.id and p.campaign_name == "Verão"
    assert p.budget == 450 and p.quantity == 2
    avisos = _avisos(db, empresa)
    assert len(avisos) == 1 and creator.name in avisos[0].message
    print("OK  - candidatura do creator: proposta pendente para a empresa dona + notificação")


def teste_a_proposta_aparece_na_lista_da_empresa_e_do_creator():
    db = dbmod.SessionLocal()
    empresa_user, empresa = _usuario(db, "company")
    creator_user, creator = _usuario(db, "creator")
    campanha = _campanha(db, empresa)
    p = _candidatar(db, creator_user, campanha)
    assert [x.id for x in list_proposals(current_user=empresa_user, db=db)] == [p.id]
    assert [x.id for x in list_proposals(current_user=creator_user, db=db)] == [p.id]
    print("OK  - a proposta do creator aparece nas listas da empresa e do próprio creator")


def teste_empresa_aceita_vincula_o_creator_ativa_e_avisa_quem_enviou():
    db = dbmod.SessionLocal()
    empresa_user, empresa = _usuario(db, "company")
    creator_user, creator = _usuario(db, "creator")
    campanha = _campanha(db, empresa, status="negotiating")
    p = _candidatar(db, creator_user, campanha)
    r = update_proposal(p.id, ProposalUpdateRequest(status="accepted"), current_user=empresa_user, db=db)
    assert r.status == "accepted"
    db.expire_all()
    campanha = CampaignRepository.get_by_id(db, campanha.id)
    assert campanha.status == "active" and CampaignRepository.creator_ids_of(campanha) == [creator.id]
    avisos = _avisos(db, creator)
    assert len(avisos) == 1 and "aceitou" in avisos[0].message and avisos[0].type == "proposalAccepted"
    assert not [a for a in _avisos(db, empresa) if "aceitou" in a.message], "a empresa não é avisada da própria resposta"
    print("OK  - empresa aceita: creator vinculado, campanha ativa, creator notificado")


def teste_empresa_recusa_nao_vincula():
    db = dbmod.SessionLocal()
    empresa_user, empresa = _usuario(db, "company")
    creator_user, creator = _usuario(db, "creator")
    campanha = _campanha(db, empresa)
    p = _candidatar(db, creator_user, campanha)
    update_proposal(p.id, ProposalUpdateRequest(status="rejected"), current_user=empresa_user, db=db)
    db.expire_all()
    assert CampaignRepository.creator_ids_of(CampaignRepository.get_by_id(db, campanha.id)) == []
    assert "recusou" in _avisos(db, creator)[0].message
    print("OK  - empresa recusa: ninguém é vinculado e o creator é avisado")


def teste_quem_responde_e_sempre_o_outro_lado():
    db = dbmod.SessionLocal()
    empresa_user, empresa = _usuario(db, "company")
    outra_user, _ = _usuario(db, "company")
    creator_user, creator = _usuario(db, "creator")
    outro_creator_user, _ = _usuario(db, "creator")
    campanha = _campanha(db, empresa)

    # candidatura do creator: nem o próprio creator, nem outra empresa, nem outro creator respondem
    cand = _candidatar(db, creator_user, campanha)
    for user in (creator_user, outra_user, outro_creator_user):
        assert _erro(lambda u=user: update_proposal(cand.id, ProposalUpdateRequest(status="accepted"), current_user=u, db=db)).status_code == 403
    db.expire_all()
    assert CampaignRepository.creator_ids_of(CampaignRepository.get_by_id(db, campanha.id)) == []

    # convite da empresa: a empresa não se auto-aprova; outro creator também não responde
    convite = create_proposal(ProposalCreateRequest(creator_id=creator.id, campaign_id=campanha.id), current_user=empresa_user, db=db)
    assert convite.sender_role == "company"
    for user in (empresa_user, outro_creator_user):
        assert _erro(lambda u=user: update_proposal(convite.id, ProposalUpdateRequest(status="accepted"), current_user=u, db=db)).status_code == 403
    update_proposal(convite.id, ProposalUpdateRequest(status="accepted"), current_user=creator_user, db=db)
    print("OK  - candidatura só a empresa dona responde; convite só o creator convidado")


def teste_proposta_respondida_nao_muda_de_novo():
    db = dbmod.SessionLocal()
    empresa_user, empresa = _usuario(db, "company")
    creator_user, _ = _usuario(db, "creator")
    campanha = _campanha(db, empresa)
    p = _candidatar(db, creator_user, campanha)
    update_proposal(p.id, ProposalUpdateRequest(status="rejected"), current_user=empresa_user, db=db)
    e = _erro(lambda: update_proposal(p.id, ProposalUpdateRequest(status="accepted"), current_user=empresa_user, db=db))
    assert e.status_code == 400 and "respondida" in e.detail
    print("OK  - proposta já respondida não pode ser aceita depois")


def teste_regras_da_candidatura():
    db = dbmod.SessionLocal()
    empresa_user, empresa = _usuario(db, "company")
    creator_user, creator = _usuario(db, "creator")
    campanha = _campanha(db, empresa)

    _candidatar(db, creator_user, campanha)
    assert _erro(lambda: _candidatar(db, creator_user, campanha)).status_code == 400, "duplicata pendente"

    assert _erro(lambda: _candidatar(db, creator_user, type("C", (), {"id": "nao-existe"})())).status_code == 404

    concluida = _campanha(db, empresa, status="completed")
    assert _erro(lambda: _candidatar(db, creator_user, concluida)).status_code == 400

    ja_dentro = _campanha(db, empresa)
    CampaignRepository.link_creator(db, ja_dentro.id, creator.id)
    assert _erro(lambda: _candidatar(db, creator_user, ja_dentro)).status_code == 400

    # só creator candidata: a rota exige o papel (feita via dependência) — a empresa usa /proposals
    print("OK  - sem duplicata pendente, campanha inexistente/concluída ou creator que já está nela")


def teste_candidatura_recusada_permite_nova_tentativa():
    db = dbmod.SessionLocal()
    empresa_user, empresa = _usuario(db, "company")
    creator_user, _ = _usuario(db, "creator")
    campanha = _campanha(db, empresa)
    p = _candidatar(db, creator_user, campanha)
    update_proposal(p.id, ProposalUpdateRequest(status="rejected"), current_user=empresa_user, db=db)
    assert _candidatar(db, creator_user, campanha, budget=300).id != p.id
    print("OK  - depois de recusada, o creator pode mandar uma nova proposta")


if __name__ == "__main__":
    for nome, fn in list(globals().items()):
        if nome.startswith("teste_"):
            fn()
    print("\nTodos os testes de propostas vindas do creator passaram.")
