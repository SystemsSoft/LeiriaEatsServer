"""
Teste de POST /ai/campaigns/confirm com influenciadores escolhidos na prévia.

Antes, o confirm ignorava `creator_ids` e criava a campanha sem nenhum vínculo em campaign_creators —
a empresa escolhia os influenciadores no app e eles não eram salvos. Agora o confirm grava o vínculo
(sem duplicatas) e recusa ids de creators que não existem.

Roda contra um SQLite em memória, sem rede.

Execução (na raiz do repo do servidor):
    python3 tests/test_conectaai_campaign_confirm_creators.py
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

from conectaai.api.routes.campaign_ai_routes import confirm_campaign_draft  # noqa: E402
from conectaai.core.security import CurrentUser  # noqa: E402
from conectaai.models import sql_models  # noqa: E402
from conectaai.schemas.ai import ConfirmCampaignDraftRequest  # noqa: E402

dbmod.Base.metadata.create_all(_engine)


_n = 0


def _cenario(db):
    """Uma empresa e dois creators — devolve (usuário da empresa, [ids dos creators])."""
    global _n
    _n += 1
    empresa_user = sql_models.UserDB(email=f"empresa{_n}@teste.com", password_hash="x", role="company", name="Empresa")
    db.add(empresa_user)
    db.flush()
    db.add(sql_models.CompanyDB(user_id=empresa_user.id, name="Empresa"))
    ids = []
    for i in range(2):
        user = sql_models.UserDB(email=f"creator{_n}-{i}@teste.com", password_hash="x", role="creator", name=f"Creator {i}")
        db.add(user)
        db.flush()
        creator = sql_models.CreatorDB(user_id=user.id, name=f"Creator {i}")
        db.add(creator)
        db.flush()
        ids.append(creator.id)
    db.commit()
    return CurrentUser(empresa_user.id, "company"), ids


def _confirmar(db, usuario, **extra):
    dados = {"campaign_name": "Verão", "objective": "Divulgar", "target_count": 2, "price_ceiling": 500}
    dados.update(extra)
    return confirm_campaign_draft(ConfirmCampaignDraftRequest(**dados), current_user=usuario, db=db)


def teste_confirm_salva_os_influenciadores_escolhidos():
    db = dbmod.SessionLocal()
    usuario, ids = _cenario(db)
    resposta = _confirmar(db, usuario, creator_ids=ids)
    assert sorted(resposta.campaign.creator_ids) == sorted(ids), resposta.campaign.creator_ids
    print("OK  - confirm grava os creators escolhidos em campaign_creators")


def teste_confirm_ignora_duplicatas():
    db = dbmod.SessionLocal()
    usuario, ids = _cenario(db)
    resposta = _confirmar(db, usuario, creator_ids=[ids[0], ids[0], ids[1]])
    assert sorted(resposta.campaign.creator_ids) == sorted(ids)
    print("OK  - creator repetido no pedido não quebra nem duplica o vínculo")


def teste_confirm_recusa_creator_inexistente():
    db = dbmod.SessionLocal()
    usuario, ids = _cenario(db)
    antes = db.query(sql_models.CampaignDB).count()
    try:
        _confirmar(db, usuario, creator_ids=[ids[0], "nao-existe"])
    except HTTPException as e:
        assert e.status_code == 400 and "nao-existe" in e.detail
    else:
        raise AssertionError("deveria recusar creator inexistente")
    assert db.query(sql_models.CampaignDB).count() == antes, "não pode criar campanha pela metade"
    print("OK  - creator inexistente: 400 e nenhuma campanha criada")


def teste_confirm_sem_creators_continua_funcionando():
    db = dbmod.SessionLocal()
    usuario, _ = _cenario(db)
    resposta = _confirmar(db, usuario)
    assert resposta.campaign.creator_ids == []
    print("OK  - confirm sem creator_ids continua criando a campanha vazia")


if __name__ == "__main__":
    for nome, fn in list(globals().items()):
        if nome.startswith("teste_"):
            fn()
