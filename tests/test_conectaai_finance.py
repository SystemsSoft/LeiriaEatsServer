"""
Teste do Financeiro: conta Stripe Connect do usuário (GET /finance/account, POST /finance/account/onboarding,
POST /finance/account/dashboard, POST /finance/stripe/webhook).

Confere: sem a chave da Stripe o app é avisado (available=false, 503); a conta é criada uma vez só e o link de
cadastro pode ser pedido de novo; endereços de retorno só https/localhost; a situação vem da Stripe (pendente →
em análise → ativa) e do aviso account.updated, com assinatura conferida; o painel só abre depois do cadastro;
cada usuário só vê a própria conta.

Roda contra SQLite em memória, sem rede — a Stripe é substituída por uma falsa.

Execução (na raiz do repo do servidor):
    python3 tests/test_conectaai_finance.py
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time

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
from conectaai.core.config import settings  # noqa: E402
from conectaai.core.security import create_access_token  # noqa: E402
from conectaai.models import sql_models as m  # noqa: E402
from conectaai.services.payments import payment_service, stripe_connect  # noqa: E402

dbmod.Base.metadata.create_all(_engine)
_client = TestClient(main.app, raise_server_exceptions=False)
_n = 0
_WEBHOOK_SECRET = "whsec_teste"


class FakeStripe:
    """Stripe falsa: guarda as contas em memória e registra o que foi pedido."""

    seq = 0

    def __init__(self):
        self.accounts = {}
        self.created = []
        self.links = []
        self.checkouts = []
        self.sessions = {}
        self.transfers = {}
        self.fail_release = None

    def create_account(self, *, email, role, user_id, country):
        FakeStripe.seq += 1  # ids únicos entre os testes (o banco é o mesmo)
        acct = f"acct_{FakeStripe.seq}"
        self.accounts[acct] = {"id": acct, "details_submitted": False, "charges_enabled": False, "payouts_enabled": False,
                               "requirements": {"currently_due": ["business_profile.url", "external_account"], "past_due": []}}
        self.created.append({"email": email, "role": role, "user_id": user_id, "country": country})
        return acct

    def create_onboarding_link(self, account_id, *, return_url, refresh_url):
        self.links.append({"account": account_id, "return_url": return_url, "refresh_url": refresh_url})
        return f"https://connect.stripe.test/setup/{account_id}/{len(self.links)}"

    def create_dashboard_link(self, account_id):
        return f"https://connect.stripe.test/express/{account_id}"

    def account_status(self, account_id):
        return stripe_connect.status_of(self.accounts[account_id])

    def create_payment_checkout(self, **kwargs):
        FakeStripe.seq += 1
        session_id = f"cs_{FakeStripe.seq}"
        self.checkouts.append({"id": session_id, **kwargs})
        self.sessions[session_id] = {"id": session_id, "payment_status": "unpaid", "status": "open", "payment_intent": None}
        return {"id": session_id, "url": f"https://checkout.stripe.test/pay/{session_id}"}

    def release_to_creator(self, **kwargs):
        if self.fail_release:
            raise stripe_connect.PaymentsError(self.fail_release)
        # mesma chave = mesmo repasse (como a Stripe faz com Idempotency-Key)
        if kwargs["idempotency_key"] not in self.transfers:
            FakeStripe.seq += 1
            self.transfers[kwargs["idempotency_key"]] = {"id": f"tr_{FakeStripe.seq}", **kwargs}
        return self.transfers[kwargs["idempotency_key"]]["id"]

    def checkout_status(self, session_id):
        return stripe_connect.checkout_status_of(self.sessions[session_id])


def _instalar_stripe(configurada=True):
    fake = FakeStripe()
    settings.STRIPE_API_KEY = "sk_test_x" if configurada else ""
    settings.STRIPE_WEBHOOK_SECRET = _WEBHOOK_SECRET
    settings.STRIPE_COUNTRY = "PT"
    settings.APP_URL = "https://app.conectaai.test"
    settings.STRIPE_CURRENCY = "eur"
    settings.PLATFORM_FEE_PERCENT = 0
    for name in ("create_account", "create_onboarding_link", "create_dashboard_link", "account_status",
                 "create_payment_checkout", "checkout_status", "release_to_creator"):
        setattr(stripe_connect, name, getattr(fake, name))
    return fake


def _usuario(role="company"):
    global _n
    _n += 1
    db = dbmod.SessionLocal()
    user = m.UserDB(email=f"{role}{_n}@teste.com", password_hash="x", role=role, name="Ana")
    db.add(user)
    db.flush()
    db.add(m.CompanyDB(user_id=user.id, name="Bela Cosméticos") if role == "company" else m.CreatorDB(user_id=user.id, name="Beto"))
    db.commit()
    return {"Authorization": f"Bearer {create_access_token(user.id, role)}"}, user.id


def _aviso(evento: dict, secret=_WEBHOOK_SECRET, quando=None):
    payload = json.dumps(evento)
    t = int(quando or time.time())
    assinatura = hmac.new(secret.encode(), f"{t}.{payload}".encode(), hashlib.sha256).hexdigest()
    return _client.post("/finance/stripe/webhook", content=payload, headers={"Stripe-Signature": f"t={t},v1={assinatura}"})


def teste_sem_a_chave_da_stripe_o_app_e_avisado():
    _instalar_stripe(configurada=False)
    empresa, _ = _usuario()
    r = _client.get("/finance/account", headers=empresa)
    assert r.status_code == 200 and r.json()["available"] is False and r.json()["status"] == "not_started"
    r = _client.post("/finance/account/onboarding", json={}, headers=empresa)
    assert r.status_code == 503, r.text
    # chave publicável no lugar da secreta também não serve
    settings.STRIPE_API_KEY = "pk_live_x"
    assert stripe_connect.is_configured() is False
    assert _client.get("/finance/account").status_code == 401  # sem login
    print("OK  - sem a chave secreta da Stripe: available=false e 503 ao tentar cadastrar")


def teste_cadastro_cria_a_conta_uma_vez_e_devolve_o_link():
    fake = _instalar_stripe()
    empresa, user_id = _usuario()
    assert _client.get("/finance/account", headers=empresa).json() == {
        "available": True, "connected": False, "status": "not_started", "details_submitted": False,
        "charges_enabled": False, "payouts_enabled": False, "requirements_due": [], "country": None,
    }

    r = _client.post("/finance/account/onboarding", headers=empresa, json={
        "return_url": "https://app.conectaai.test/#/finance?stripe=return",
        "refresh_url": "https://app.conectaai.test/#/finance?stripe=refresh",
    })
    assert r.status_code == 200 and r.json()["url"].startswith("https://connect.stripe.test/setup/acct_"), r.text
    assert fake.created == [{"email": fake.created[0]["email"], "role": "company", "user_id": user_id, "country": "PT"}]
    assert fake.links[0]["return_url"].endswith("stripe=return") and fake.links[0]["refresh_url"].endswith("stripe=refresh")

    # pedir de novo (link expirou, continuar depois): mesma conta, link novo
    r2 = _client.post("/finance/account/onboarding", json={}, headers=empresa)
    assert r2.status_code == 200 and len(fake.created) == 1 and len(fake.links) == 2
    assert fake.links[1]["return_url"] == "https://app.conectaai.test/#/finance?stripe=return"  # sem endereço: o do app configurado

    conta = _client.get("/finance/account", headers=empresa).json()
    assert conta["connected"] is True and conta["status"] == "pending" and conta["country"] == "PT"
    assert conta["requirements_due"] == ["business_profile.url", "external_account"]
    print("OK  - cadastro: conta criada uma vez, link de uso único, situação 'pending' com as pendências")


def teste_endereco_de_retorno_precisa_ser_do_app():
    fake = _instalar_stripe()
    empresa, _ = _usuario()
    for ruim in ("javascript:alert(1)", "http://site-qualquer.test/x", "ftp://x.test"):
        r = _client.post("/finance/account/onboarding", json={"return_url": ruim, "refresh_url": ruim}, headers=empresa)
        assert r.status_code == 400, (ruim, r.text)
    assert fake.created == []  # nada foi criado na Stripe
    r = _client.post("/finance/account/onboarding", headers=empresa, json={
        "return_url": "http://localhost:5000/#/finance", "refresh_url": "http://localhost:5000/#/finance"})
    assert r.status_code == 200  # desenvolvimento local
    print("OK  - endereço de retorno: só https ou localhost")


def teste_situacao_acompanha_a_stripe_e_o_painel_so_abre_depois_do_cadastro():
    fake = _instalar_stripe()
    empresa, _ = _usuario()
    _client.post("/finance/account/onboarding", json={}, headers=empresa)
    acct = fake.links[-1]["account"]
    assert _client.post("/finance/account/dashboard", headers=empresa).status_code == 409  # cadastro incompleto

    # enviou o cadastro, a Stripe ainda analisa
    fake.accounts[acct].update(details_submitted=True, requirements={"currently_due": [], "past_due": ["individual.verification.document"]})
    conta = _client.get("/finance/account", headers=empresa).json()
    assert conta["status"] == "restricted" and conta["requirements_due"] == ["individual.verification.document"]
    r = _client.post("/finance/account/dashboard", headers=empresa)
    assert r.status_code == 200 and r.json()["url"].endswith(acct)

    # aprovada
    fake.accounts[acct].update(charges_enabled=True, payouts_enabled=True, requirements={"currently_due": [], "past_due": []})
    conta = _client.get("/finance/account", headers=empresa).json()
    assert conta["status"] == "active" and conta["charges_enabled"] and conta["payouts_enabled"] and conta["requirements_due"] == []

    # Stripe fora do ar: devolve o último estado guardado
    def _fora(_):
        raise stripe_connect.PaymentsError("fora do ar")
    stripe_connect.account_status = _fora
    assert _client.get("/finance/account", headers=empresa).json()["status"] == "active"
    print("OK  - situação: pending → restricted → active; painel só após o cadastro; Stripe fora do ar usa o estado guardado")


def teste_aviso_account_updated_atualiza_a_conta_e_confere_a_assinatura():
    fake = _instalar_stripe()
    empresa, _ = _usuario()
    _client.post("/finance/account/onboarding", json={}, headers=empresa)
    acct = fake.links[-1]["account"]
    aprovado = {"id": acct, "details_submitted": True, "charges_enabled": True, "payouts_enabled": True, "requirements": {"currently_due": []}}
    evento = {"id": "evt_1", "type": "account.updated", "data": {"object": aprovado}}

    assert _aviso(evento, secret="whsec_errado").status_code == 400
    assert _aviso(evento, quando=time.time() - 3600).status_code == 400  # aviso antigo reenviado
    assert _client.post("/finance/stripe/webhook", content=json.dumps(evento)).status_code == 400  # sem assinatura

    assert _aviso(evento).status_code == 200
    db = dbmod.SessionLocal()
    conta = db.query(m.PaymentAccountDB).filter_by(stripe_account_id=acct).first()
    assert conta.charges_enabled and conta.payouts_enabled and conta.details_submitted
    # conta desconhecida e outros eventos não quebram
    assert _aviso({"id": "evt_2", "type": "account.updated", "data": {"object": {"id": "acct_de_outro"}}}).status_code == 200
    assert _aviso({"id": "evt_3", "type": "payout.paid", "data": {"object": {"id": "po_1"}}}).status_code == 200
    print("OK  - aviso account.updated: assinatura conferida e situação atualizada")


def teste_le_o_objeto_real_da_biblioteca_da_stripe():
    """A resposta de verdade da API não é um dict (nem tem .get): a leitura não pode depender disso."""
    import stripe

    real = stripe.Account.construct_from(
        {"id": "acct_real", "details_submitted": True, "charges_enabled": True, "payouts_enabled": False,
         "requirements": {"currently_due": ["external_account"], "past_due": ["external_account", "tos_acceptance.date"]}},
        "sk_test_x",
    )
    assert stripe_connect.status_of(real) == {
        "details_submitted": True, "charges_enabled": True, "payouts_enabled": False,
        "requirements_due": ["external_account", "tos_acceptance.date"],
    }
    assert stripe_connect.status_of({"id": "acct_x"})["requirements_due"] == []
    print("OK  - lê a resposta real da biblioteca da Stripe (objeto, não dict)")


def teste_cada_usuario_tem_a_propria_conta_e_o_creator_tambem_cadastra():
    fake = _instalar_stripe()
    empresa, _ = _usuario("company")
    creator, _ = _usuario("creator")
    _client.post("/finance/account/onboarding", json={}, headers=empresa)
    assert _client.get("/finance/account", headers=creator).json()["connected"] is False
    _client.post("/finance/account/onboarding", json={}, headers=creator)
    assert [c["role"] for c in fake.created] == ["company", "creator"]
    assert fake.links[0]["account"] != fake.links[1]["account"]
    print("OK  - cada usuário com a própria conta; creator também cadastra (é quem recebe)")


# --- Pagamento dos acordos -------------------------------------------------------


def _acordo(fake, *, budget=1500.0, status="accepted", creator_ativo=True):
    """Empresa, creator e uma proposta — devolve (headers_empresa, headers_creator, proposal_id, ids)."""
    global _n
    _n += 1
    db = dbmod.SessionLocal()
    eu = m.UserDB(email=f"emp{_n}@teste.com", password_hash="x", role="company", name="Ana Diretora")
    cu = m.UserDB(email=f"cre{_n}@teste.com", password_hash="x", role="creator", name="Beto Creator")
    db.add_all([eu, cu])
    db.flush()
    empresa = m.CompanyDB(user_id=eu.id, name="Bela Cosméticos")
    creator = m.CreatorDB(user_id=cu.id, name="Beto Creator", username="@beto")
    db.add_all([empresa, creator])
    db.flush()
    proposta = m.ProposalDB(company_id=empresa.id, creator_id=creator.id, campaign_name="Verão 2026", content_type="Reels",
                            quantity=2, budget=budget, status=status, message="oi")
    db.add(proposta)
    db.commit()
    h = lambda user, role: {"Authorization": f"Bearer {create_access_token(user.id, role)}"}  # noqa: E731
    emp, cre = h(eu, "company"), h(cu, "creator")
    acct = None
    if creator_ativo:
        _client.post("/finance/account/onboarding", json={}, headers=cre)
        acct = fake.links[-1]["account"]
        fake.accounts[acct].update(details_submitted=True, charges_enabled=True, payouts_enabled=True, requirements={})
        assert _client.get("/finance/account", headers=cre).json()["status"] == "active"
    return emp, cre, proposta.id, {"company_user": eu.id, "creator_user": cu.id, "acct": acct}


def _avisos(user_id):
    db = dbmod.SessionLocal()
    return [(n.title, n.message) for n in db.query(m.NotificationDB).filter_by(user_id=user_id).all()]


def _sessao_paga(fake, session_id):
    fake.sessions[session_id].update(payment_status="paid", status="complete", payment_intent="pi_1")
    return {"id": "evt_p", "type": "checkout.session.completed", "data": {"object": fake.sessions[session_id]}}


def _pagar(fake, emp, pid):
    """A empresa paga e a Stripe confirma: o valor fica reservado. Devolve o pedido feito à Stripe."""
    r = _client.post("/finance/payments", json={"proposal_id": pid}, headers=emp)
    assert r.status_code == 200, r.text
    pedido = fake.checkouts[-1]
    assert _aviso(_sessao_paga(fake, pedido["id"])).status_code == 200
    return pedido


def teste_empresa_paga_e_o_valor_fica_reservado_ate_ela_confirmar():
    fake = _instalar_stripe()
    settings.PLATFORM_FEE_PERCENT = 10
    emp, cre, pid, ids = _acordo(fake, budget=1500.0)

    r = _client.post("/finance/payments", headers=emp, json={
        "proposal_id": pid, "success_url": "https://app.conectaai.test/#/collaborations/x?payment=ok",
        "cancel_url": "https://app.conectaai.test/#/collaborations/x?payment=cancel"})
    assert r.status_code == 200 and r.json()["url"].startswith("https://checkout.stripe.test/pay/cs_"), r.text
    pedido = fake.checkouts[-1]
    # a cobrança entra na plataforma: nada de destino nem de taxa na cobrança — o repasse é um passo separado
    assert pedido["amount_cents"] == 150000 and pedido["currency"] == "eur"
    assert "destination" not in pedido and "fee_cents" not in pedido
    assert pedido["transfer_group"] == f"conectaai_proposal_{pid}" and pedido["metadata"] == {"conectaai_proposal_id": pid}
    assert _client.get(f"/collaborations/{pid}", headers=cre).json()["payment_status"] == "pending"
    # antes de a Stripe confirmar, não há o que liberar
    assert _client.post(f"/finance/payments/{pid}/release", headers=emp).status_code == 409

    # a Stripe avisa que pagou (duas vezes: aviso repetido não duplica nada)
    evento = _sessao_paga(fake, pedido["id"])
    assert _aviso(evento).status_code == 200 and _aviso(evento).status_code == 200
    assert _client.get(f"/collaborations/{pid}", headers=emp).json()["payment_status"] == "paid"
    assert fake.transfers == {}, "pago, mas ainda reservado: nada foi para o creator"
    reservado = [a for a in _avisos(ids["creator_user"]) if a[0] == "Pagamento reservado"]
    assert len(reservado) == 1 and "1.350,00 € estão reservados" in reservado[0][1] and "quando a empresa confirmar" in reservado[0][1]
    assert [t for t, _ in _avisos(ids["company_user"])].count("Pagamento confirmado") == 1
    assert _client.post("/finance/payments", json={"proposal_id": pid}, headers=emp).status_code == 409  # não paga duas vezes

    # a empresa confirma que o acordo foi cumprido: o valor (menos a taxa) vai para a conta do creator
    r = _client.post(f"/finance/payments/{pid}/release", headers=emp)
    assert r.status_code == 200 and r.json()["status"] == "released" and r.json()["released_at"], r.text
    repasse = list(fake.transfers.values())[0]
    assert repasse["amount_cents"] == 135000 and repasse["destination"] == ids["acct"] and repasse["payment_intent_id"] == "pi_1"
    assert repasse["transfer_group"] == f"conectaai_proposal_{pid}"
    assert _client.get(f"/collaborations/{pid}", headers=cre).json()["payment_status"] == "released"
    liberado = [a for a in _avisos(ids["creator_user"]) if a[0] == "Pagamento liberado"]
    assert len(liberado) == 1 and "liberou 1.350,00 €" in liberado[0][1]

    # confirmar de novo (clique duplo) não repassa de novo nem avisa de novo
    assert _client.post(f"/finance/payments/{pid}/release", headers=emp).json()["status"] == "released"
    assert len(fake.transfers) == 1
    assert len([a for a in _avisos(ids["creator_user"]) if a[0] == "Pagamento liberado"]) == 1
    assert _client.post("/finance/payments", json={"proposal_id": pid}, headers=emp).status_code == 409

    # histórico: a empresa vê o que pagou; o creator, o líquido
    da_empresa = _client.get("/finance/payments", headers=emp).json()
    assert len(da_empresa) == 1 and da_empresa[0]["amount"] == 1500.0 and da_empresa[0]["status"] == "released"
    assert da_empresa[0]["counterpart_name"] == "Beto Creator" and da_empresa[0]["campaign_name"] == "Verão 2026"
    do_creator = _client.get("/finance/payments", headers=cre).json()
    assert do_creator[0]["net"] == 1350.0 and do_creator[0]["fee"] == 150.0 and do_creator[0]["counterpart_name"] == "Bela Cosméticos"
    print("OK  - retenção: pago fica reservado (sem repasse); a confirmação da empresa libera o líquido; sem repasse em dobro")


def teste_creator_sem_conta_a_empresa_paga_mas_so_libera_com_a_conta_ativa():
    fake = _instalar_stripe()
    emp, cre, pid, ids = _acordo(fake, budget=300.0, creator_ativo=False)
    _pagar(fake, emp, pid)  # paga mesmo sem a conta do creator: o valor fica reservado
    reservado = [m_ for t, m_ in _avisos(ids["creator_user"]) if t == "Pagamento reservado"]
    assert len(reservado) == 1 and "Cadastre sua conta de pagamentos" in reservado[0]

    for _ in range(2):
        r = _client.post(f"/finance/payments/{pid}/release", headers=emp)
        assert r.status_code == 409 and "ainda não tem a conta de pagamentos ativa" in r.json()["detail"], r.text
    assert fake.transfers == {}
    assert [t for t, _ in _avisos(ids["creator_user"])].count("Cadastre sua conta para receber") == 1  # avisado uma vez só
    assert _client.get(f"/collaborations/{pid}", headers=emp).json()["payment_status"] == "paid"  # continua reservado

    # o creator cadastra e a conta é aprovada: agora libera
    _client.post("/finance/account/onboarding", json={}, headers=cre)
    acct = fake.links[-1]["account"]
    fake.accounts[acct].update(details_submitted=True, charges_enabled=True, payouts_enabled=True, requirements={})
    _client.get("/finance/account", headers=cre)
    assert _client.post(f"/finance/payments/{pid}/release", headers=emp).json()["status"] == "released"
    assert list(fake.transfers.values())[0]["destination"] == acct and list(fake.transfers.values())[0]["amount_cents"] == 30000
    print("OK  - creator sem conta: a empresa paga (reservado), mas só libera quando a conta dele está ativa")


def teste_falha_da_stripe_na_liberacao_mantem_o_valor_reservado():
    fake = _instalar_stripe()
    emp, cre, pid, ids = _acordo(fake)
    _pagar(fake, emp, pid)
    fake.fail_release = "Saldo insuficiente na plataforma"
    r = _client.post(f"/finance/payments/{pid}/release", headers=emp)
    assert r.status_code == 502 and "Saldo insuficiente" in r.json()["detail"]
    assert _client.get(f"/collaborations/{pid}", headers=emp).json()["payment_status"] == "paid"
    assert [t for t, _ in _avisos(ids["creator_user"])].count("Pagamento liberado") == 0
    fake.fail_release = None
    assert _client.post(f"/finance/payments/{pid}/release", headers=emp).json()["status"] == "released"
    print("OK  - a Stripe recusa o repasse: nada muda (continua reservado) e dá para tentar de novo")


def teste_conferencia_ao_voltar_e_nova_tentativa_depois_de_expirar():
    fake = _instalar_stripe()
    emp, cre, pid, ids = _acordo(fake, budget=200.0)
    assert _client.post(f"/finance/payments/{pid}/sync", headers=emp).status_code == 404  # ainda sem pagamento

    _client.post("/finance/payments", json={"proposal_id": pid}, headers=emp)
    primeira = fake.checkouts[-1]["id"]
    assert _client.post(f"/finance/payments/{pid}/sync", headers=emp).json()["status"] == "pending"

    # a página expirou sem pagar: o acordo volta a "não pago" e a empresa tenta de novo
    fake.sessions[primeira].update(status="expired")
    assert _client.post(f"/finance/payments/{pid}/sync", headers=emp).json()["status"] == "canceled"
    assert _client.get(f"/collaborations/{pid}", headers=emp).json()["payment_status"] == "unpaid"
    assert _client.post(f"/finance/payments/{pid}/release", headers=emp).status_code == 409  # nada pago para liberar
    assert _client.post("/finance/payments", json={"proposal_id": pid}, headers=emp).status_code == 200
    segunda = fake.checkouts[-1]["id"]
    assert segunda != primeira

    # pagou, e o aviso da Stripe ainda não chegou: a conferência ao voltar já confirma
    fake.sessions[segunda].update(payment_status="paid", status="complete", payment_intent={"id": "pi_9"})
    pago = _client.post(f"/finance/payments/{pid}/sync", headers=cre).json()
    assert pago["status"] == "paid" and pago["net"] == 200.0 and pago["released_at"] is None
    db = dbmod.SessionLocal()
    linha = db.query(m.PaymentDB).filter_by(proposal_id=pid).all()
    assert len(linha) == 1 and linha[0].payment_intent_id == "pi_9"  # um registro por acordo
    # aviso atrasado da primeira sessão (expirada) não desfaz o pagamento
    _aviso({"id": "evt_x", "type": "checkout.session.expired", "data": {"object": fake.sessions[primeira]}})
    assert _client.get(f"/collaborations/{pid}", headers=emp).json()["payment_status"] == "paid"
    print("OK  - conferência ao voltar da Stripe, sessão expirada libera nova tentativa, um registro por acordo")


def teste_so_a_empresa_dona_paga_e_libera_e_so_acordo_aceito_com_valor():
    fake = _instalar_stripe()
    emp, cre, pid, _ = _acordo(fake)
    outra_emp, _, _, _ = _acordo(fake)
    assert _client.post("/finance/payments", json={"proposal_id": pid}, headers=cre).status_code == 403
    assert _client.post("/finance/payments", json={"proposal_id": pid}, headers=outra_emp).status_code == 404
    assert _client.post(f"/finance/payments/{pid}/sync", headers=outra_emp).status_code == 404
    assert _client.post("/finance/payments", json={"proposal_id": "nao-existe"}, headers=emp).status_code == 404

    emp2, _, pendente, _ = _acordo(fake, status="pending")
    assert _client.post("/finance/payments", json={"proposal_id": pendente}, headers=emp2).status_code == 404
    emp3, _, sem_valor, _ = _acordo(fake, budget=0)
    r = _client.post("/finance/payments", json={"proposal_id": sem_valor}, headers=emp3)
    assert r.status_code == 409 and "valor definido" in r.json()["detail"]
    r = _client.post("/finance/payments", headers=emp, json={"proposal_id": pid, "success_url": "http://golpe.test", "cancel_url": "http://golpe.test"})
    assert r.status_code == 400
    assert fake.checkouts == []

    # liberar: o creator não libera o próprio pagamento, nem outra empresa
    _pagar(fake, emp, pid)
    assert _client.post(f"/finance/payments/{pid}/release", headers=cre).status_code == 403
    assert _client.post(f"/finance/payments/{pid}/release", headers=outra_emp).status_code == 404
    assert fake.transfers == {}

    settings.STRIPE_API_KEY = ""
    assert _client.post("/finance/payments", json={"proposal_id": pid}, headers=emp).status_code == 503
    assert _client.post(f"/finance/payments/{pid}/release", headers=emp).status_code == 503
    print("OK  - só a empresa dona paga e libera (o creator não); só acordo aceito e com valor; sem Stripe = 503")


def teste_dois_segredos_de_webhook_e_objetos_reais_da_biblioteca():
    fake = _instalar_stripe()
    settings.STRIPE_WEBHOOK_SECRET = "whsec_contas, whsec_pagamentos"
    emp, _, pid, _ = _acordo(fake)
    _client.post("/finance/payments", json={"proposal_id": pid}, headers=emp)
    evento = _sessao_paga(fake, fake.checkouts[-1]["id"])
    assert _aviso(evento, secret="whsec_outro").status_code == 400
    assert _aviso(evento, secret="whsec_pagamentos").status_code == 200
    assert _client.get(f"/collaborations/{pid}", headers=emp).json()["payment_status"] == "paid"

    import stripe
    real = stripe.checkout.Session.construct_from({"id": "cs_real", "payment_status": "paid", "status": "complete", "payment_intent": "pi_real"}, "sk_test_x")
    assert stripe_connect.checkout_status_of(real) == {"paid": True, "expired": False, "payment_intent": "pi_real"}
    assert payment_service.money(123456) == "1.234,56 €" and payment_service.money(5) == "0,05 €"
    print("OK  - aviso vale com qualquer um dos dois segredos; lê a sessão real da biblioteca; formato do dinheiro")


def teste_repasse_real_usa_a_cobranca_original_e_chave_de_idempotencia():
    """release_to_creator de verdade (sem a Stripe falsa), com as chamadas da biblioteca trocadas por registradores."""
    import importlib
    import stripe

    importlib.reload(stripe_connect)  # desfaz as funções falsas instaladas pelos outros testes
    settings.STRIPE_API_KEY = "sk_test_x"
    chamadas = {}
    original_intent, original_transfer = stripe.PaymentIntent.retrieve, stripe.Transfer.create
    try:
        stripe.PaymentIntent.retrieve = lambda pi, **kw: stripe.PaymentIntent.construct_from({"id": pi, "latest_charge": "ch_77"}, "sk_test_x")

        def _transfer(**kw):
            chamadas.update(kw)
            return stripe.Transfer.construct_from({"id": "tr_77"}, "sk_test_x")

        stripe.Transfer.create = _transfer
        tr = stripe_connect.release_to_creator(
            amount_cents=135000, currency="eur", destination="acct_9", payment_intent_id="pi_77",
            transfer_group="conectaai_proposal_x", idempotency_key="conectaai_release_p1", metadata={"k": "v"})
        assert tr == "tr_77"
        assert chamadas["source_transaction"] == "ch_77" and chamadas["destination"] == "acct_9" and chamadas["amount"] == 135000
        assert chamadas["idempotency_key"] == "conectaai_release_p1" and chamadas["transfer_group"] == "conectaai_proposal_x"

        # cobrança ainda não registrada: erro claro, sem repasse
        stripe.PaymentIntent.retrieve = lambda pi, **kw: stripe.PaymentIntent.construct_from({"id": pi, "latest_charge": None}, "sk_test_x")
        try:
            stripe_connect.release_to_creator(
                amount_cents=1, currency="eur", destination="acct_9", payment_intent_id="pi_0",
                transfer_group="g", idempotency_key="k", metadata={})
            raise AssertionError("deveria recusar")
        except stripe_connect.PaymentsError as exc:
            assert "ainda não registrou a cobrança" in str(exc)
    finally:
        stripe.PaymentIntent.retrieve, stripe.Transfer.create = original_intent, original_transfer
    print("OK  - repasse real: sai da cobrança original (source_transaction), com chave de idempotência")


if __name__ == "__main__":
    teste_sem_a_chave_da_stripe_o_app_e_avisado()
    teste_cadastro_cria_a_conta_uma_vez_e_devolve_o_link()
    teste_endereco_de_retorno_precisa_ser_do_app()
    teste_situacao_acompanha_a_stripe_e_o_painel_so_abre_depois_do_cadastro()
    teste_aviso_account_updated_atualiza_a_conta_e_confere_a_assinatura()
    teste_le_o_objeto_real_da_biblioteca_da_stripe()
    teste_cada_usuario_tem_a_propria_conta_e_o_creator_tambem_cadastra()
    teste_empresa_paga_e_o_valor_fica_reservado_ate_ela_confirmar()
    teste_creator_sem_conta_a_empresa_paga_mas_so_libera_com_a_conta_ativa()
    teste_falha_da_stripe_na_liberacao_mantem_o_valor_reservado()
    teste_conferencia_ao_voltar_e_nova_tentativa_depois_de_expirar()
    teste_so_a_empresa_dona_paga_e_libera_e_so_acordo_aceito_com_valor()
    teste_dois_segredos_de_webhook_e_objetos_reais_da_biblioteca()
    teste_repasse_real_usa_a_cobranca_original_e_chave_de_idempotencia()  # por último: recarrega o módulo da Stripe
