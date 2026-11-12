"""
Revisão de segurança de 12/11/2026 — senhas com hash, token interno, CORS e docs fechados.

Execução:
    python3 tests/test_seguranca_basica.py
"""
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")
os.environ["ADMIN_TOKEN"] = "token-de-teste-123"
os.environ["CORS_ORIGINS"] = "https://api.leiriaeats.com"
os.environ.pop("EXPOR_DOCS", None)

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core import senhas
from core.database import Base, get_db
from core.sql_models import RestaurantDB
from repositories.restaurant_repo import RestaurantRepository
from schemas.company import CompanyCreateRequest

motor = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(bind=motor)
Sessao = sessionmaker(bind=motor)


def teste_hash_verifica_e_rehash():
    h = senhas.gerar_hash("segredo")
    assert h.startswith("pbkdf2_sha256$310000$") and h != senhas.gerar_hash("segredo")  # salt diferente
    assert senhas.verificar("segredo", h) and not senhas.verificar("errada", h)
    assert not senhas.precisa_rehash(h)
    # estafeta antigo: sha256 sem salt
    import hashlib
    antigo = hashlib.sha256(b"segredo").hexdigest()
    assert senhas.verificar("segredo", antigo) and senhas.precisa_rehash(antigo)
    # restaurante antigo: texto simples só quando pedido
    assert senhas.verificar("segredo", "segredo", aceitar_texto_simples=True)
    assert not senhas.verificar("segredo", "segredo")
    assert not senhas.verificar("segredo", "pbkdf2_sha256$lixo")
    print("OK  - hash PBKDF2 com salt, verificação, sha256 antigo e texto simples só quando pedido")


def teste_restaurante_novo_grava_hash_e_antigo_e_convertido_no_login():
    with Sessao() as db:
        novo = RestaurantRepository.create_company(db, CompanyCreateRequest(
            name="Novo", category="X", phone="1", address="a", login="novo@x.pt", password="abc123", license="ATIVO"))
        assert novo.password.startswith("pbkdf2_sha256$") and "abc123" not in novo.password
        db.add(RestaurantDB(name="Antigo", category="X", login="antigo@x.pt", password="texto-simples", license="ATIVO"))
        db.commit()
        assert RestaurantRepository.check_credentials(db, "novo@x.pt", "abc123") is not None
        assert RestaurantRepository.check_credentials(db, "novo@x.pt", "xxx") is None
        assert RestaurantRepository.check_credentials(db, "antigo@x.pt", "texto-simples") is not None
        guardada = db.query(RestaurantDB).filter_by(login="antigo@x.pt").one().password
        assert guardada.startswith("pbkdf2_sha256$"), guardada
        assert RestaurantRepository.check_credentials(db, "antigo@x.pt", "texto-simples") is not None
    print("OK  - registo grava hash; senha antiga em texto simples entra e fica convertida no 1.º login")


def _app_rotas_internas():
    import api.routes.chat_routes as chat
    import api.routes.search_routes as search
    from services.ai_service import AIService
    from services.hybrid_ai_service import HybridAIService
    from services.session_service import SessionManager
    AIService.reload_data = classmethod(lambda cls, db: None)
    HybridAIService.get_status = classmethod(lambda cls: {"system_ready": True, "e5_loaded": True, "gemini_ready": True})
    SessionManager.get_stats = classmethod(lambda cls: {"active_sessions": 3})
    app = FastAPI()
    app.include_router(chat.router)
    app.include_router(search.router)
    app.dependency_overrides[get_db] = lambda: iter([None])
    return TestClient(app)


def teste_token_interno():
    c = _app_rotas_internas()
    assert c.post("/search/reload").status_code == 401
    assert c.post("/search/reload", headers={"X-Admin-Token": "errado"}).status_code == 401
    assert c.post("/search/reload", headers={"X-Admin-Token": "token-de-teste-123"}).status_code == 200
    publico = c.get("/chat/status").json()
    assert publico == {"status": "ready"}, publico
    detalhe = c.get("/chat/status", headers={"X-Admin-Token": "token-de-teste-123"}).json()
    assert detalhe["active_sessions"] == 3 and "details" in detalhe
    print("OK  - /search/reload só com X-Admin-Token; /chat/status público só diz ready/loading")


def teste_cors_e_docs_fechados():
    import main
    c = TestClient(main.app)
    r = c.get("/restaurants/categories", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers, dict(r.headers)
    r = c.options("/restaurants/categories", headers={"Origin": "https://api.leiriaeats.com",
                                                      "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") == "https://api.leiriaeats.com"
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
    print("OK  - CORS só para a origem configurada; /docs e /openapi.json fechados")


if __name__ == "__main__":
    teste_hash_verifica_e_rehash()
    teste_restaurante_novo_grava_hash_e_antigo_e_convertido_no_login()
    teste_token_interno()
    teste_cors_e_docs_fechados()
    print("\nTodos os testes de segurança básica passaram.")
