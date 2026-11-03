"""
Testes das rotas do app do estafeta (KomaPartner) — api/routes/drivers.py.

Cobre o que o app depende do servidor: identificação pelo gid, estado de cada paragem,
"Pedido pronto" carregado depois da oferta, código de entrega e cancelamentos.

Execução:
    python3 tests/test_rotas_estafeta.py
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base
from core.sql_models import OrderDB, SubOrderDB, DriverDB, DeliveryRouteDB, RouteStopDB
from api.routes import drivers
from schemas.driver import RouteCompleteRequest
import services.courier_notification_service as dispatch

DRIVER_GID = "01DRV_PARTNER"
ROUTE_GID = "01ROUTE_PARTNER"
TRACKING_CODE = "482913577"


def _montar_cenario(route_status="IN_PROGRESS"):
    """Pedido com 2 restaurantes numa rota já aceite pelo estafeta."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    driver = DriverDB(gid=DRIVER_GID, login="estafeta", password="x", status="ACTIVE", name="Estafeta")
    master = OrderDB(
        gid="01ORD_PARTNER", customer_name="Cliente", delivery_address="Rua Cliente",
        delivery_latitude=39.74, delivery_longitude=-8.80, delivery_type="delivery",
        status="Em preparo", total=30.0, tracking_code=TRACKING_CODE,
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


if __name__ == "__main__":
    teste_rotas_por_gid_com_estado_e_sem_codigo()
    teste_pedido_pronto_depois_da_oferta_aparece_ao_estafeta()
    teste_conclusao_exige_codigo_do_cliente()
    teste_conclusao_exige_todas_as_recolhas()
    teste_cancelamento_parcial_nao_bloqueia_nem_vira_entregue()
    teste_pedido_cancelado_liberta_o_estafeta()
    print("\nTodos os testes das rotas do estafeta passaram.")
