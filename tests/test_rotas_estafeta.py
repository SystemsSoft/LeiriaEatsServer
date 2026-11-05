"""
Testes das rotas do app do estafeta (KomaPartner) — api/routes/drivers.py.

Cobre o que o app depende do servidor: identificação pelo gid, estado de cada paragem,
"Pedido pronto" carregado depois da oferta, código de entrega e cancelamentos.

Execução:
    python3 tests/test_rotas_estafeta.py
"""
import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base
from core.sql_models import OrderDB, SubOrderDB, DriverDB, DeliveryRouteDB, RouteStopDB, RestaurantDB
from api.routes import drivers
from schemas.driver import (
    RouteCompleteRequest, DriverRegisterRequest, DriverLoginRequest,
    DriverPersonalInfoDto, DriverVehicleInfoDto,
)
import services.courier_notification_service as dispatch
import services.driver_payout_service as payout

DRIVER_GID = "01DRV_PARTNER"
ROUTE_GID = "01ROUTE_PARTNER"
TRACKING_CODE = "482913577"


def _montar_cenario(route_status="IN_PROGRESS"):
    """Pedido com 2 restaurantes numa rota já aceite pelo estafeta."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    driver = DriverDB(
        gid=DRIVER_GID, login="estafeta", password="x", status="ACTIVE", name="Estafeta",
        stripe_account_id="acct_estafeta", stripe_onboarding_completed=True,
    )
    master = OrderDB(
        gid="01ORD_PARTNER", customer_name="Cliente", delivery_address="Rua Cliente",
        delivery_latitude=39.74, delivery_longitude=-8.80, delivery_type="delivery",
        status="Em preparo", total=30.0, tracking_code=TRACKING_CODE, payment_intent_id="pi_teste",
        created_at=datetime.now(timezone.utc),
    )
    db.add_all([driver, master])
    db.commit()

    subs = [
        SubOrderDB(
            gid=f"01SUB_{letra}", master_order_gid=master.gid, restaurant_gid=f"01R_{letra}",
            restaurant_name=f"Restaurante {letra}", restaurant_latitude=39.7, restaurant_longitude=-8.8,
            status="A aguardar estafeta", total=15.0, base_time=10,
        )
        for letra in ("A", "B")
    ]
    db.add_all(subs)
    route = DeliveryRouteDB(
        gid=ROUTE_GID, master_order_gid=master.gid, driver_gid=DRIVER_GID, status=route_status,
        offered_at=datetime.now(timezone.utc), estimated_fee=4.2,
    )
    db.add(route)
    db.commit()
    db.add_all([
        RouteStopDB(route_gid=ROUTE_GID, sub_order_gid=sub.gid, sequence=i)
        for i, sub in enumerate(subs, start=1)
    ])
    db.commit()
    return db, master, subs


def _recolher_tudo(db, subs):
    for sub in subs:
        drivers.mark_stop_picked_up(ROUTE_GID, sub.id, DRIVER_GID, db)


def _espera_http(status_code, fn):
    try:
        fn()
    except HTTPException as exc:
        assert exc.status_code == status_code, (exc.status_code, exc.detail)
        return exc
    raise AssertionError(f"esperava HTTP {status_code}")


def teste_rotas_por_gid_com_estado_e_sem_codigo():
    db, _, _ = _montar_cenario()
    rotas = drivers.get_driver_routes(DRIVER_GID, db)
    assert len(rotas) == 1
    assert "tracking_code" not in rotas[0]
    assert [p["status"] for p in rotas[0]["stops"]] == ["A aguardar estafeta"] * 2
    print("OK  - GET /drivers/routes aceita o gid, devolve o estado de cada paragem e não expõe o código")


def teste_pedido_pronto_depois_da_oferta_aparece_ao_estafeta():
    db, _, subs = _montar_cenario()
    pronto = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
    subs[0].ready_at = pronto
    db.commit()
    paragens = drivers.get_driver_routes(DRIVER_GID, db)[0]["stops"]
    assert paragens[0]["ready_at_confirmed"] is not None
    assert paragens[1]["ready_at_confirmed"] is None
    print("OK  - 'Pedido pronto' carregado depois da oferta chega ao app")


def teste_conclusao_exige_codigo_do_cliente():
    db, master, subs = _montar_cenario()
    _recolher_tudo(db, subs)

    erro = _espera_http(400, lambda: drivers.complete_route(ROUTE_GID, DRIVER_GID, None, db))
    assert "Código" in erro.detail
    _espera_http(400, lambda: drivers.complete_route(ROUTE_GID, DRIVER_GID, RouteCompleteRequest(code="000000000"), db))

    drivers.complete_route(ROUTE_GID, DRIVER_GID, RouteCompleteRequest(code=" 482 913 577 "), db)
    assert db.query(DeliveryRouteDB).filter_by(gid=ROUTE_GID).first().status == "COMPLETED"
    assert master.status == "Entregue"
    print("OK  - entrega só conclui com o código certo do cliente (ignora espaços)")


def teste_conclusao_exige_todas_as_recolhas():
    db, _, subs = _montar_cenario()
    drivers.mark_stop_picked_up(ROUTE_GID, subs[0].id, DRIVER_GID, db)
    _espera_http(409, lambda: drivers.complete_route(
        ROUTE_GID, DRIVER_GID, RouteCompleteRequest(code=TRACKING_CODE), db))
    print("OK  - entrega não conclui com recolhas por fazer")


def teste_cancelamento_parcial_nao_bloqueia_nem_vira_entregue():
    db, master, subs = _montar_cenario()
    subs[1].status = "Cancelado"
    db.commit()

    _espera_http(409, lambda: drivers.mark_stop_picked_up(ROUTE_GID, subs[1].id, DRIVER_GID, db))
    drivers.mark_stop_picked_up(ROUTE_GID, subs[0].id, DRIVER_GID, db)
    drivers.complete_route(ROUTE_GID, DRIVER_GID, RouteCompleteRequest(code=TRACKING_CODE), db)

    assert subs[0].status == "Entregue"
    assert subs[1].status == "Cancelado"
    assert master.status == "Entregue"
    print("OK  - restaurante cancelado não bloqueia a entrega e continua 'Cancelado'")


def teste_pedido_cancelado_liberta_o_estafeta():
    db, master, subs = _montar_cenario(route_status="ACCEPTED")
    master.status = "Cancelado"
    for sub in subs:
        sub.status = "Cancelado"
    db.commit()

    assert drivers.get_driver_routes(DRIVER_GID, db) == []
    assert db.query(DeliveryRouteDB).filter_by(gid=ROUTE_GID).first().status == "CANCELLED"
    ocupados = [gid for (gid,) in dispatch._busy_driver_gids_subquery(db).all()]
    assert DRIVER_GID not in ocupados
    print("OK  - pedido cancelado fecha a rota e o estafeta volta a receber ofertas")


class _StripeFalso:
    """Substitui as chamadas Stripe usadas no pagamento ao estafeta."""

    def __init__(self, pi_status="succeeded"):
        self.pi_status = pi_status
        self.transfers = []

    def __enter__(self):
        self._orig = (payout.stripe.PaymentIntent.retrieve, payout.stripe.Transfer.create)
        payout.stripe.PaymentIntent.retrieve = lambda _id: {"status": self.pi_status, "latest_charge": "ch_teste"}

        def criar(**kwargs):
            self.transfers.append(kwargs)
            return SimpleNamespace(id=f"tr_{len(self.transfers)}")

        payout.stripe.Transfer.create = criar
        return self

    def __exit__(self, *exc):
        payout.stripe.PaymentIntent.retrieve, payout.stripe.Transfer.create = self._orig


# Nenhum teste pode falar com a Stripe real (o .env local tem chave configurada): por
# omissão o pagamento do cliente aparece "por capturar" e nada é transferido.
_StripeFalso(pi_status="requires_capture").__enter__()


def teste_concluir_paga_o_estafeta_uma_so_vez():
    db, _, subs = _montar_cenario()
    _recolher_tudo(db, subs)
    with _StripeFalso() as stripe_falso:
        drivers.complete_route(ROUTE_GID, DRIVER_GID, RouteCompleteRequest(code=TRACKING_CODE), db)
        rota = db.query(DeliveryRouteDB).filter_by(gid=ROUTE_GID).first()
        payout.retry_pending_payouts(db)  # já pago: não pode transferir de novo

    assert len(stripe_falso.transfers) == 1
    t = stripe_falso.transfers[0]
    assert t["amount"] == 420 and t["destination"] == "acct_estafeta"
    assert t["source_transaction"] == "ch_teste"
    assert t["idempotency_key"] == f"transfer_route_{ROUTE_GID}"
    assert {s.driver_payment_transfer_id for s in subs} == {"tr_1"}
    assert sorted(s.driver_delivery_fee for s in subs) == [2.1, 2.1]
    assert payout.is_route_paid(rota)
    print("OK  - concluir a rota transfere a taxa ao estafeta (uma só vez)")


def teste_pagamento_pendente_e_retentado():
    db, _, subs = _montar_cenario()
    _recolher_tudo(db, subs)
    with _StripeFalso(pi_status="requires_capture") as stripe_falso:
        drivers.complete_route(ROUTE_GID, DRIVER_GID, RouteCompleteRequest(code=TRACKING_CODE), db)
    assert stripe_falso.transfers == []

    with _StripeFalso() as stripe_falso:
        payout.retry_pending_payouts(db)
    assert len(stripe_falso.transfers) == 1
    print("OK  - pagamento por capturar fica pendente e o worker volta a tentar")


def teste_paragem_cancelada_nao_recebe_parte_da_taxa():
    db, _, subs = _montar_cenario()
    subs[1].status = "Cancelado"
    db.commit()
    drivers.mark_stop_picked_up(ROUTE_GID, subs[0].id, DRIVER_GID, db)
    with _StripeFalso() as stripe_falso:
        drivers.complete_route(ROUTE_GID, DRIVER_GID, RouteCompleteRequest(code=TRACKING_CODE), db)
    assert stripe_falso.transfers[0]["amount"] == 420
    assert subs[0].driver_delivery_fee == 4.2 and subs[1].driver_payment_transfer_id is None
    print("OK  - a taxa inteira fica no sub-pedido entregue")


def teste_palavra_passe_antiga_e_convertida_no_login():
    db, _, _ = _montar_cenario()
    driver = db.query(DriverDB).filter_by(gid=DRIVER_GID).first()
    driver.password = hashlib.sha256(b"segredo").hexdigest()
    db.commit()

    _espera_http(401, lambda: drivers.login_driver(DriverLoginRequest(login="estafeta", password="errada"), db))
    assert drivers.login_driver(DriverLoginRequest(login="estafeta", password="segredo"), db).authenticated
    assert driver.password.startswith("pbkdf2_sha256$")
    assert drivers.login_driver(DriverLoginRequest(login="estafeta", password="segredo"), db).authenticated
    print("OK  - palavra-passe sha256 antiga entra e é convertida para PBKDF2 com salt")


def teste_registo_guarda_perfil_e_fica_pendente():
    db, _, _ = _montar_cenario()
    resposta = drivers.register_driver(DriverRegisterRequest(
        login="novo@estafeta.pt", password="123456",
        personal_info=DriverPersonalInfoDto(name="Rui Novo", phone="912345678"),
        vehicle_info=DriverVehicleInfoDto(type="MOTORCYCLE", plate="AA-00-BB"),
    ), db)
    assert resposta["status"] == "PENDING" and resposta["profile_complete"] is True
    novo = drivers.get_driver_profile(resposta["gid"], db)
    assert (novo.name, novo.vehicle_type, novo.vehicle_plate) == ("Rui Novo", "MOTORCYCLE", "AA-00-BB")
    assert novo.password.startswith("pbkdf2_sha256$")
    _espera_http(400, lambda: drivers.register_driver(DriverRegisterRequest(login="x", password="123"), db))
    print("OK  - registo guarda dados pessoais/veículo e exige palavra-passe com 6+ caracteres")


def teste_retorno_da_stripe_ativa_a_conta():
    db, _, _ = _montar_cenario()
    driver = db.query(DriverDB).filter_by(gid=DRIVER_GID).first()
    driver.status, driver.stripe_onboarding_completed = "STRIPE_PENDING", False
    db.commit()
    original = drivers.stripe.Account.retrieve
    drivers.stripe.Account.retrieve = lambda _id: SimpleNamespace(
        details_submitted=True, charges_enabled=True, payouts_enabled=True)
    try:
        pagina = drivers.driver_stripe_onboarding_success(DRIVER_GID, db)
    finally:
        drivers.stripe.Account.retrieve = original
    assert "Pagamentos configurados" in pagina.body.decode()
    assert driver.status == "ACTIVE" and driver.stripe_onboarding_completed
    print("OK  - página de retorno da Stripe ativa a conta do estafeta")


def teste_restaurante_com_estafeta_proprio_nao_entra_no_despacho():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add_all([
        RestaurantDB(name="Proprio", category="X", login="p", password="x", gid="01R_PROPRIO", use_own_delivery=True),
        RestaurantDB(name="Koma", category="X", login="k", password="x", gid="01R_KOMA", use_own_delivery=False),
        OrderDB(gid="01ORD_X", customer_name="C", delivery_type="delivery", status="Em preparo", total=10.0,
                created_at=datetime.now(timezone.utc) - timedelta(minutes=30)),
    ])
    db.commit()
    db.add_all([
        SubOrderDB(gid=f"01SUB_{r}", master_order_gid="01ORD_X", restaurant_gid=f"01R_{r}", restaurant_name=r,
                   status="Em Preparo", total=5.0, base_time=5)
        for r in ("PROPRIO", "KOMA")
    ])
    db.commit()
    elegiveis = dispatch._eligible_subs_grouped_by_master(db)["01ORD_X"]
    assert [s.restaurant_gid for s in elegiveis] == ["01R_KOMA"]
    print("OK  - restaurante com estafeta próprio fica fora do despacho Koma")


if __name__ == "__main__":
    teste_rotas_por_gid_com_estado_e_sem_codigo()
    teste_pedido_pronto_depois_da_oferta_aparece_ao_estafeta()
    teste_conclusao_exige_codigo_do_cliente()
    teste_conclusao_exige_todas_as_recolhas()
    teste_cancelamento_parcial_nao_bloqueia_nem_vira_entregue()
    teste_pedido_cancelado_liberta_o_estafeta()
    teste_concluir_paga_o_estafeta_uma_so_vez()
    teste_pagamento_pendente_e_retentado()
    teste_paragem_cancelada_nao_recebe_parte_da_taxa()
    teste_palavra_passe_antiga_e_convertida_no_login()
    teste_registo_guarda_perfil_e_fica_pendente()
    teste_retorno_da_stripe_ativa_a_conta()
    teste_restaurante_com_estafeta_proprio_nao_entra_no_despacho()
    print("\nTodos os testes das rotas do estafeta passaram.")
