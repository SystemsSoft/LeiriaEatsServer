"""
Testes de integração do despacho por rota (PLANO_RECOLHA_MULTI_RESTAURANTE.md, Fase 3).

`_check_and_notify` usa `SessionLocal()` internamente (não recebe `db` por parâmetro) —
por isso este teste monkeypatcha `services.courier_notification_service.SessionLocal`
para apontar para um banco SQLite em memória, seguindo o mesmo padrão já usado em
tests/test_cinto_seguranca_autorizacao.py e tests/test_reconciliacao_pagamentos.py.

Execução:
    python3 tests/test_dispatch_rota.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base
from core.sql_models import OrderDB, SubOrderDB, DriverDB, DeliveryRouteDB, RouteStopDB
import services.courier_notification_service as dispatch


def _montar_sessionmaker_sqlite():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _com_session_de_teste(fn):
    """Monkeypatcha SessionLocal, roda fn(Session), sempre restaura no final."""
    Session = _montar_sessionmaker_sqlite()
    original = dispatch.SessionLocal
    dispatch.SessionLocal = Session
    try:
        fn(Session)
    finally:
        dispatch.SessionLocal = original


def _criar_pedido_dois_restaurantes(db, gid="01ORD_TESTE", base_time=5, status="Em Preparo"):
    master = OrderDB(
        gid=gid, customer_name="Cliente Teste", delivery_address="Rua Cliente",
        delivery_latitude=38.720, delivery_longitude=-9.140, delivery_type="delivery",
        status="Em preparo", total=30.0, created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )
    db.add(master)
    db.commit()
    db.refresh(master)

    sub_a = SubOrderDB(
        gid=f"{gid}_SUB_A", master_order_gid=master.gid, restaurant_gid="01R_A",
        restaurant_name="Restaurante A", restaurant_category="Pizzaria",
        restaurant_latitude=38.716, restaurant_longitude=-9.139,
        status=status, total=15.0, base_time=base_time,
    )
    sub_b = SubOrderDB(
        gid=f"{gid}_SUB_B", master_order_gid=master.gid, restaurant_gid="01R_B",
        restaurant_name="Restaurante B", restaurant_category="Sushi",
        restaurant_latitude=38.718, restaurant_longitude=-9.145,
        status=status, total=15.0, base_time=base_time,
    )
    db.add_all([sub_a, sub_b])
    db.commit()
    db.refresh(sub_a)
    db.refresh(sub_b)
    return master, sub_a, sub_b


def _criar_estafeta_disponivel(db, gid="01DRV_TESTE", lat=38.717, lon=-9.141):
    driver = DriverDB(
        gid=gid, login=f"login_{gid}", password="x", status="ACTIVE", name="Estafeta Teste",
        latitude=lat, longitude=lon, last_seen=datetime.now(timezone.utc),
    )
    db.add(driver)
    db.commit()
    db.refresh(driver)
    return driver


def teste_oferece_rota_para_pedido_com_dois_restaurantes_prontos():
    def cenario(Session):
        db = Session()
        master, sub_a, sub_b = _criar_pedido_dois_restaurantes(db)
        driver = _criar_estafeta_disponivel(db)
        db.close()

        dispatch._check_and_notify()

        db = Session()
        rotas = db.query(DeliveryRouteDB).all()
        assert len(rotas) == 1, f"esperava 1 rota, achou {len(rotas)}"
        rota = rotas[0]
        assert rota.status == "OFFERED", rota.status
        assert rota.driver_gid == driver.gid
        assert rota.estimated_fee is not None and rota.estimated_fee >= 2.50, rota.estimated_fee

        stops = db.query(RouteStopDB).filter(RouteStopDB.route_gid == rota.gid).order_by(RouteStopDB.sequence).all()
        assert len(stops) == 2, f"esperava 2 paragens, achou {len(stops)}"
        assert {s.sub_order_gid for s in stops} == {sub_a.gid, sub_b.gid}
        assert [s.sequence for s in stops] == [1, 2]

        subs_atualizados = db.query(SubOrderDB).filter(SubOrderDB.master_order_gid == master.gid).all()
        assert all(s.status == "Oferta enviada" for s in subs_atualizados), [s.status for s in subs_atualizados]
        assert all(s.driver_name == "Estafeta Teste" for s in subs_atualizados)
        db.close()

    _com_session_de_teste(cenario)
    print("OK  - pedido com 2 restaurantes prontos gera 1 rota com 2 paragens sequenciadas")


def teste_nao_duplica_oferta_quando_pedido_ja_tem_rota_ativa():
    def cenario(Session):
        db = Session()
        master, sub_a, sub_b = _criar_pedido_dois_restaurantes(db, status="Oferta enviada")
        driver = _criar_estafeta_disponivel(db)
        rota_existente = DeliveryRouteDB(
            gid="01ROUTE_EXISTENTE", master_order_gid=master.gid, driver_gid=driver.gid,
            status="OFFERED", offered_at=datetime.now(timezone.utc),
        )
        db.add(rota_existente)
        db.commit()
        db.close()

        dispatch._check_and_notify()

        db = Session()
        rotas = db.query(DeliveryRouteDB).all()
        assert len(rotas) == 1, f"esperava continuar com 1 rota (não duplicar), achou {len(rotas)}"
        db.close()

    _com_session_de_teste(cenario)
    print("OK  - pedido com rota já ativa não gera uma segunda oferta")


def teste_estafeta_ocupado_nao_e_escolhido_para_outro_pedido():
    def cenario(Session):
        db = Session()
        master1, _, _ = _criar_pedido_dois_restaurantes(db, gid="01ORD_A")
        master2, _, _ = _criar_pedido_dois_restaurantes(db, gid="01ORD_B")
        driver = _criar_estafeta_disponivel(db)
        # Estafeta já está numa rota ativa para o pedido A
        db.add(DeliveryRouteDB(
            gid="01ROUTE_A", master_order_gid=master1.gid, driver_gid=driver.gid,
            status="ACCEPTED", offered_at=datetime.now(timezone.utc), accepted_at=datetime.now(timezone.utc),
        ))
        db.commit()
        db.close()

        dispatch._check_and_notify()

        db = Session()
        rotas_pedido_b = db.query(DeliveryRouteDB).filter(DeliveryRouteDB.master_order_gid == master2.gid).all()
        assert len(rotas_pedido_b) == 0, "estafeta ocupado não deveria ter sido atribuído a um 2º pedido"
        db.close()

    _com_session_de_teste(cenario)
    print("OK  - estafeta com rota ativa não é escolhido para um pedido diferente")


def teste_expira_oferta_sem_aceite_e_devolve_sub_pedidos_ao_pool():
    """
    Testa _expire_pending_routes isoladamente (não _check_and_notify) — se fosse o ciclo
    completo, o mesmo poll que expira a rota também re-ofereceria imediatamente para o
    estafeta que acabou de ficar livre (comportamento correto, coberto por
    teste_oferece_rota_..., mas que mascararia esta asserção específica).
    """
    def cenario(Session):
        db = Session()
        master, sub_a, sub_b = _criar_pedido_dois_restaurantes(db, status="Oferta enviada")
        driver = _criar_estafeta_disponivel(db)
        offered_at_vencido = datetime.now(timezone.utc) - timedelta(seconds=dispatch.ACCEPT_TIMEOUT_SECONDS + 30)
        rota = DeliveryRouteDB(
            gid="01ROUTE_VENCIDA", master_order_gid=master.gid, driver_gid=driver.gid,
            status="OFFERED", offered_at=offered_at_vencido,
        )
        db.add(rota)
        db.commit()
        db.add_all([
            RouteStopDB(route_gid=rota.gid, sub_order_gid=sub_a.gid, sequence=1),
            RouteStopDB(route_gid=rota.gid, sub_order_gid=sub_b.gid, sequence=2),
        ])
        db.commit()

        dispatch._expire_pending_routes(db, datetime.now(timezone.utc))

        rota_db = db.query(DeliveryRouteDB).filter(DeliveryRouteDB.gid == "01ROUTE_VENCIDA").first()
        assert rota_db.status == "EXPIRED", rota_db.status
        assert rota_db.driver_gid is None

        subs = db.query(SubOrderDB).filter(SubOrderDB.master_order_gid == master.gid).all()
        assert all(s.status == "Em preparo" for s in subs), [s.status for s in subs]
        assert all(s.driver_name is None for s in subs)
        db.close()

    _com_session_de_teste(cenario)
    print("OK  - oferta expirada sem aceite devolve sub-pedidos a 'Em preparo' e libera o estafeta")


if __name__ == "__main__":
    teste_oferece_rota_para_pedido_com_dois_restaurantes_prontos()
    teste_nao_duplica_oferta_quando_pedido_ja_tem_rota_ativa()
    teste_estafeta_ocupado_nao_e_escolhido_para_outro_pedido()
    teste_expira_oferta_sem_aceite_e_devolve_sub_pedidos_ao_pool()
    print("\nTodos os testes de despacho por rota (Fase 3) passaram.")
