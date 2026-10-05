"""
Teste de erros do servidor chegando ao navegador.

1. POST /proposals com creator_id inexistente: 404 claro (antes era erro de FK do MySQL, um 500).
2. Exceção não tratada: 500 em JSON COM o cabeçalho de CORS. Sem ele, o navegador esconde o erro real
   atrás de "blocked by CORS policy". Cada erro leva um `error_id`, que também vai para o log do servidor.

Roda contra um SQLite em memória (com FK ativa, como o InnoDB), sem rede.

Execução (na raiz do repo do servidor):
    python3 tests/test_conectaai_error_handling.py
"""
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CONECTAAI_GEMINI_API_KEY", "")
os.environ["CONECTAAI_UPLOAD_DIR"] = tempfile.mkdtemp()

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import conectaai.core.database as dbmod

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


@event.listens_for(_engine, "connect")
def _foreign_keys_on(dbapi, _):
    dbapi.execute("PRAGMA foreign_keys=ON")


dbmod.engine = _engine
dbmod.SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

from fastapi.testclient import TestClient  # noqa: E402

import conectaai.main as main  # noqa: E402
from conectaai.core.security import create_access_token  # noqa: E402
from conectaai.models import sql_models  # noqa: E402

dbmod.Base.metadata.create_all(_engine)

ORIGIN = "http://localhost:49762"


@main.app.get("/_teste/explode")
def _explode():
    raise RuntimeError("falha inesperada de teste")


_client = TestClient(main.app, raise_server_exceptions=False)


def _cenario():
    db = dbmod.SessionLocal()
    empresa = sql_models.UserDB(email="empresa@teste.com", password_hash="x", role="company", name="Empresa")
    db.add(empresa)
    db.flush()
    db.add(sql_models.CompanyDB(user_id=empresa.id, name="Empresa"))
    user = sql_models.UserDB(email="creator@teste.com", password_hash="x", role="creator", name="Creator")
    db.add(user)
    db.flush()
    creator = sql_models.CreatorDB(user_id=user.id, name="Creator")
    db.add(creator)
    db.commit()
    headers = {"Authorization": f"Bearer {create_access_token(empresa.id, 'company')}", "Origin": ORIGIN}
    return headers, creator.id


def _proposta(creator_id):
    return {"creator_id": creator_id, "campaign_name": "Verão", "content_type": "Reels", "quantity": 2, "budget": 500, "message": "oi"}


def teste_proposta_para_creator_inexistente_e_404_com_cors():
    headers, _ = _cenario()
    r = _client.post("/proposals", json=_proposta("nao-existe"), headers=headers)
    assert r.status_code == 404 and r.json()["detail"] == "Creator não encontrado", (r.status_code, r.text)
    assert r.headers.get("access-control-allow-origin") == ORIGIN
    print("OK  - creator inexistente: 404 'Creator não encontrado' (antes: 500)")


def teste_proposta_valida_continua_criando_e_notificando():
    headers, creator_id = _cenario_existente()
    r = _client.post("/proposals", json=_proposta(creator_id), headers=headers)
    assert r.status_code == 201, (r.status_code, r.text)
    db = dbmod.SessionLocal()
    assert db.query(sql_models.NotificationDB).count() == 1
    print("OK  - proposta válida: 201 e o creator é notificado")


def _cenario_existente():
    db = dbmod.SessionLocal()
    empresa = db.query(sql_models.UserDB).filter_by(role="company").first()
    creator = db.query(sql_models.CreatorDB).first()
    return {"Authorization": f"Bearer {create_access_token(empresa.id, 'company')}", "Origin": ORIGIN}, creator.id


def teste_erro_nao_tratado_volta_em_json_com_cors_e_vai_para_o_log():
    registros = []

    class _Captura(logging.Handler):
        def emit(self, record):
            registros.append(record)

    handler = _Captura()
    logging.getLogger("conectaai").addHandler(handler)
    try:
        r = _client.get("/_teste/explode", headers={"Origin": ORIGIN})
    finally:
        logging.getLogger("conectaai").removeHandler(handler)

    assert r.status_code == 500, r.status_code
    assert r.headers.get("access-control-allow-origin") == ORIGIN, dict(r.headers)
    corpo = r.json()
    assert "falha inesperada" not in r.text, "o texto interno da exceção não pode vazar para o cliente"
    assert len(corpo["error_id"]) == 8
    assert any(corpo["error_id"] in rec.getMessage() and rec.exc_info for rec in registros), "traceback precisa ir para o log com o error_id"
    print("OK  - erro inesperado: 500 em JSON com CORS, error_id no corpo e traceback no log")


def teste_erros_tratados_seguem_iguais():
    headers, _ = _cenario_existente()
    r = _client.post("/proposals", json=_proposta("qualquer") | {"campaign_id": "nao-existe"}, headers=headers)
    # creator inexistente vem antes da campanha
    assert r.status_code == 404
    r = _client.get("/proposals", headers={"Origin": ORIGIN})
    assert r.status_code == 401 and r.headers.get("access-control-allow-origin") == ORIGIN
    print("OK  - 401/404 continuam como antes, com CORS")


if __name__ == "__main__":
    teste_proposta_para_creator_inexistente_e_404_com_cors()
    teste_proposta_valida_continua_criando_e_notificando()
    teste_erro_nao_tratado_volta_em_json_com_cors_e_vai_para_o_log()
    teste_erros_tratados_seguem_iguais()
