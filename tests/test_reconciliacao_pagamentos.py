"""
Testes de `_reconcile_pending_payments` (services/payment_reconciliation_service.py) —
a rotina de redundância que confirma a autorização de um pedido MANUAL_CAPTURE quando o
webhook payment_intent.amount_capturable_updated se perde.

BUG real de produção (2026-08-28): sub-pedido nasce com status="PENDING_PAYMENT"
(nunca None). A condição que promovia o sub para "Pendente" quando esta rotina (e não o
webhook) confirmava a autorização checava `sub.status is None`, que nunca é verdade — o
sub-pedido ficava travado em "PENDING_PAYMENT" para sempre, mesmo depois de aceito,
capturado e repassado com sucesso. Corrigido para checar "PENDING_PAYMENT" (o valor real
de criação) também.

Execução:
    python3 tests/test_reconciliacao_pagamentos.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

import stripe
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta, timezone

from core.database import Base
from core.sql_models import OrderDB, SubOrderDB
import services.payment_reconciliation_service as reconciliation


def _montar_sessionmaker_sqlite():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _pedido_pending_payment(db, gid):
    """Reproduz o estado real logo após o checkout: master em PENDING_PAYMENT,
    sub-pedido também em PENDING_PAYMENT (nunca None) — antes de qualquer webhook
    confirmar a autorização."""
    master = OrderDB(
        gid=gid, customer_name="Cliente", delivery_address="Rua X", status="PENDING_PAYMENT",
        total=20.0, user_id="U-1", payment_intent_id=f"pi_{gid}", payment_flow="MANUAL_CAPTURE",
        payment_status="REQUIRES_PAYMENT", created_at=datetime.now(timezone.utc),
    )
    db.add(master)
    db.commit()
    db.refresh(master)
    sub = SubOrderDB(gid=f"{gid}_SUB", master_order_gid=master.gid, restaurant_gid="01R_A",
                      status="PENDING_PAYMENT", total=20.0)
    db.add(sub)
    db.commit()
    return master


class _StripeRetrieveMock:
    """Simula stripe.PaymentIntent.retrieve devolvendo o status informado."""

    def __init__(self, status):
        self._status = status

    def __enter__(self):
        self._original = stripe.PaymentIntent.retrieve
        status = self._status

        def fake_retrieve(pi_id, **kwargs):
            class _Fake:
                id = pi_id
                pass
            fake = _Fake()
            fake.status = status
            return fake

        stripe.PaymentIntent.retrieve = fake_retrieve
        return self

    def __exit__(self, *exc):
        stripe.PaymentIntent.retrieve = self._original


def teste_reconciliacao_promove_sub_pedido_de_pending_payment_para_pendente():
    """O caso que quebrou em produção: quando é ESTA rotina (redundância), e não o
    webhook, quem confirma requires_capture, o sub-pedido tem que sair de
    PENDING_PAYMENT — não pode ficar preso nesse status para sempre."""
    Session = _montar_sessionmaker_sqlite()
    db_setup = Session()
    _pedido_pending_payment(db_setup, "01ORDER_REDUNDANCIA")
    db_setup.close()

    original_session_local = reconciliation.SessionLocal
    reconciliation.SessionLocal = Session
    try:
        with _StripeRetrieveMock("requires_capture"):
            asyncio.run(reconciliation._reconcile_pending_payments())
    finally:
        reconciliation.SessionLocal = original_session_local

    db_check = Session()
    master = db_check.query(OrderDB).filter(OrderDB.gid == "01ORDER_REDUNDANCIA").first()
    assert master.payment_status == "AUTHORIZED"
    assert master.status == "Pendente"
    sub = db_check.query(SubOrderDB).filter(SubOrderDB.master_order_gid == "01ORDER_REDUNDANCIA").first()
    assert sub.status == "Pendente", (
        f"sub-pedido ficou preso em '{sub.status}' — regressão do bug de produção "
        "(PENDING_PAYMENT nunca avança porque a condição checava `is None`)"
    )
    print("OK  - sub-pedido sai de PENDING_PAYMENT para Pendente quando a redundância "
          "(não o webhook) confirma a autorização")


def teste_reconciliacao_nao_mexe_em_sub_pedido_ja_respondido():
    """Sanidade: a promoção para 'Pendente' não deve pisar num sub-pedido que já foi
    além de PENDING_PAYMENT por outro caminho (ex.: já cancelado antes desta rotina
    rodar)."""
    Session = _montar_sessionmaker_sqlite()
    db_setup = Session()
    master = _pedido_pending_payment(db_setup, "01ORDER_JA_CANCELADO")
    sub = master.sub_orders[0]
    sub.status = "Cancelado"
    sub.declined_at = datetime.now(timezone.utc)
    db_setup.commit()
    db_setup.close()

    original_session_local = reconciliation.SessionLocal
    reconciliation.SessionLocal = Session
    try:
        with _StripeRetrieveMock("requires_capture"):
            asyncio.run(reconciliation._reconcile_pending_payments())
    finally:
        reconciliation.SessionLocal = original_session_local

    db_check = Session()
    sub_check = db_check.query(SubOrderDB).filter(SubOrderDB.master_order_gid == "01ORDER_JA_CANCELADO").first()
    assert sub_check.status == "Cancelado"
    print("OK  - sub-pedido já cancelado não é sobrescrito para 'Pendente' pela redundância")


if __name__ == "__main__":
    teste_reconciliacao_promove_sub_pedido_de_pending_payment_para_pendente()
    teste_reconciliacao_nao_mexe_em_sub_pedido_ja_respondido()
    print("\nTodos os testes de reconciliação de pagamentos passaram.")
