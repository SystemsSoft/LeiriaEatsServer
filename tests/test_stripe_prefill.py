"""
Testes do pré-preenchimento das contas Stripe (services/stripe_prefill.py) e do login de
restaurantes com o cadastro na Stripe por concluir. A Stripe é um dublê: nenhuma chamada real.

Execução:
    python3 tests/test_stripe_prefill.py
"""
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

import stripe
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace

import api.routes.auth_routes as auth
from core.database import Base, get_db
from core.senhas import gerar_hash
from core.sql_models import DriverDB, RestaurantDB
from services import stripe_prefill as sp


def teste_telefone_e164():
    casos = {
        "912 345 678": "+351912345678", "912345678": "+351912345678", "+351 912 345 678": "+351912345678",
        "00351912345678": "+351912345678", "+44 7911 123456": "+447911123456",
        "": None, None: None, "12345": None, "abc": None, "1234567890123": None,
    }
    for entrada, esperado in casos.items():
        assert sp.telefone_e164(entrada) == esperado, (entrada, sp.telefone_e164(entrada), esperado)
    print("OK  - telefones para o formato internacional; os duvidosos ficam de fora")


def teste_dados_do_restaurante_e_do_estafeta():
    r = SimpleNamespace(name="Pizzaria do João", phone="912 345 678")
    d = sp.dados_restaurante(r)
    assert d == {"business_profile": {
        "name": "Pizzaria do João", "mcc": "5812",
        "product_description": d["business_profile"]["product_description"],
        "support_phone": "+351912345678"}}, d
    assert "business_type" not in d  # pode ser empresa ou empresário em nome individual: a Stripe pergunta
    sem_telefone = sp.dados_restaurante(SimpleNamespace(name="X", phone=None))
    assert "support_phone" not in sem_telefone["business_profile"]

    e = SimpleNamespace(name="Ana Maria Silva", phone="912345678", address="Rua A 1", city="Leiria", postal_code="2400-001")
    d = sp.dados_estafeta(e, "ana@x.pt")
    assert d["business_type"] == "individual" and d["business_profile"]["mcc"] == "4215"
    assert d["individual"] == {"first_name": "Ana", "last_name": "Maria Silva", "email": "ana@x.pt",
                               "phone": "+351912345678",
                               "address": {"line1": "Rua A 1", "city": "Leiria", "postal_code": "2400-001", "country": "PT"}}, d
    minimo = sp.dados_estafeta(SimpleNamespace(name="Ana", phone=None, address=None, city=None, postal_code=None), "a@x.pt")
    assert "first_name" not in minimo["individual"] and minimo["individual"]["address"] == {"country": "PT"}
    print("OK  - dados do restaurante (atividade, nome, telefone) e do estafeta (pessoa singular, morada)")


def teste_criar_conta_com_pre_preenchimento_e_recuo():
    chamadas = []
    original = stripe.Account.create

    def com_sucesso(**kw):
        chamadas.append(kw)
        return SimpleNamespace(id="acct_1")

    stripe.Account.create = com_sucesso
    try:
        sp.criar_conta_express("a@x.pt", {"business_profile": {"mcc": "5812"}})
        assert chamadas[0]["type"] == "express" and chamadas[0]["country"] == "PT"
        assert chamadas[0]["business_profile"] == {"mcc": "5812"} and chamadas[0]["email"] == "a@x.pt"

        chamadas.clear()

        def recusa_o_pre_preenchimento(**kw):
            chamadas.append(kw)
            if "business_profile" in kw:
                raise stripe.error.InvalidRequestError("Invalid phone", "business_profile")
            return SimpleNamespace(id="acct_2")

        stripe.Account.create = recusa_o_pre_preenchimento
        conta = sp.criar_conta_express("a@x.pt", {"business_profile": {"support_phone": "xx"}})
        assert conta.id == "acct_2" and len(chamadas) == 2 and "business_profile" not in chamadas[1], chamadas

        chamadas.clear()
        stripe.Account.create = recusa_o_pre_preenchimento
        sp.criar_conta_express("a@x.pt")  # sem pré-preenchimento: uma só chamada
        assert len(chamadas) == 1

        # a Stripe recusa um só campo: retira-se esse e mantém-se o resto do pré-preenchimento
        chamadas.clear()

        def recusa_o_codigo_postal(**kw):
            chamadas.append(kw)
            if kw.get("individual", {}).get("address", {}).get("postal_code"):
                raise stripe.error.InvalidRequestError("Invalid PT postal code", "individual[address][postal_code]")
            return SimpleNamespace(id="acct_3")

        stripe.Account.create = recusa_o_codigo_postal
        extras = {"business_type": "individual", "business_profile": {"mcc": "4215"},
                  "individual": {"first_name": "Ana", "address": {"line1": "Rua A", "postal_code": "9999-999", "country": "PT"}}}
        conta = sp.criar_conta_express("a@x.pt", extras)
        assert conta.id == "acct_3" and len(chamadas) == 2, chamadas
        ultima = chamadas[1]
        assert ultima["business_profile"] == {"mcc": "4215"} and ultima["individual"]["first_name"] == "Ana", ultima
        assert ultima["individual"]["address"] == {"line1": "Rua A", "country": "PT"}, ultima
        assert extras["individual"]["address"]["postal_code"] == "9999-999"  # o original não é alterado

        # erro sem param (ou que não dá para localizar): conta só com o mínimo
        chamadas.clear()

        def recusa_sem_param(**kw):
            chamadas.append(kw)
            if "individual" in kw:
                raise stripe.error.InvalidRequestError("algo inválido", None)
            return SimpleNamespace(id="acct_4")

        stripe.Account.create = recusa_sem_param
        sp.criar_conta_express("a@x.pt", {"individual": {"first_name": "Ana"}})
        assert len(chamadas) == 2 and "individual" not in chamadas[1]

        def erro_de_rede(**kw):
            raise stripe.error.APIConnectionError("rede")

        stripe.Account.create = erro_de_rede
        try:
            sp.criar_conta_express("a@x.pt", {"business_profile": {"mcc": "5812"}})
            raise AssertionError("devia propagar erros que não são de validação")
        except stripe.error.APIConnectionError:
            pass
    finally:
        stripe.Account.create = original
    print("OK  - conta criada com o pré-preenchimento; um campo recusado é retirado e o resto fica; sem saber qual, só o mínimo")


def _cliente_login():
    motor = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=motor)
    Sessao = sessionmaker(bind=motor)
    with Sessao() as db:
        def rest(login, status, licenca, completo=False, gid=None):
            db.add(RestaurantDB(name=f"R {login}", category="X", login=login, password=gerar_hash("senha"),
                                license=licenca, status=status, gid=gid or f"G-{login}",
                                stripe_onboarding_completed=completo))
        rest("ativo", "ACTIVE", "ATIVO", completo=True)
        rest("pendente", "PENDING", "PENDING")
        rest("stripe", "STRIPE_PENDING", "PENDING")
        rest("inativo", "INACTIVE", "INATIVO")
        db.commit()
    app = FastAPI()
    app.include_router(auth.router)

    def _db():
        with Sessao() as db:
            yield db

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def teste_login_com_cadastro_stripe_por_concluir():
    c = _cliente_login()

    def entrar(login, senha="senha"):
        return c.post("/login", json={"login": login, "password": senha})

    r = entrar("ativo").json()
    assert r["authenticated"] is True and r["gid"] == "G-ativo" and r["stripe_pending"] is False, r
    for login in ("pendente", "stripe"):
        r = entrar(login).json()
        assert r["authenticated"] is False and r["stripe_pending"] is True and r["gid"] == f"G-{login}", r
    r = entrar("inativo").json()
    assert r["authenticated"] is False and r["stripe_pending"] is False and r["gid"] == "", r
    assert entrar("stripe", "errada").status_code == 401  # sem a senha certa não há gid nem oferta
    print("OK  - /login: ativo entra; cadastro Stripe por concluir recebe gid + stripe_pending; inativo e senha errada não")


if __name__ == "__main__":
    teste_telefone_e164()
    teste_dados_do_restaurante_e_do_estafeta()
    teste_criar_conta_com_pre_preenchimento_e_recuo()
    teste_login_com_cadastro_stripe_por_concluir()
    print("\nTodos os testes do pré-preenchimento Stripe passaram.")
