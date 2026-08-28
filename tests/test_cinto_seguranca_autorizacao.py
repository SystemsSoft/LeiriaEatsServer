"""
Teste do cinto de segurança de autorizações presas (PLANO_PAGAMENTO_2_ETAPAS.md, Fase 6.1).

Diferente das outras rotinas do worker, `_cancelar_autorizacoes_presas` cria sua própria
sessão via `SessionLocal()` (não recebe `db` por parâmetro) — por isso este teste
monkeypatcha `services.payment_reconciliation_service.SessionLocal` para apontar para o
SQLite em memória, em vez de passar `db` diretamente como nos outros testes de pagamento.

Execução:
    python3 tests/test_cinto_seguranca_autorizacao.py
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

import stripe
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base
from core.sql_models import OrderDB, SubOrderDB
import services.payment_reconciliation_service as reconciliation


def _montar_sessionmaker_sqlite():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _pedido_authorized(db, gid, minutos_desde_expiracao):
    """minutos_desde_expiracao positivo = já passou do prazo; negativo = ainda dentro."""
    expira_em = datetime.now(timezone.utc) - timedelta(minutes=minutos_desde_expiracao)
    master = OrderDB(
        gid=gid, customer_name="Cliente", delivery_address="Rua X", status="Pendente",
        total=20.0, user_id="U-1", payment_intent_id=f"pi_{gid}", payment_flow="MANUAL_CAPTURE",
        payment_status="AUTHORIZED", authorization_expires_at=expira_em,
    )
    db.add(master)
    db.commit()
    db.refresh(master)
    sub = SubOrderDB(gid=f"{gid}_SUB", master_order_gid=master.gid, restaurant_gid="01R_A",
                      status="Pendente", total=20.0)
    db.add(sub)
    db.commit()
    return master


class _StripeCancelMock:
    def __enter__(self):
        self._original = stripe.PaymentIntent.cancel
        self.chamadas = []
        def fake_cancel(pi_id, **kwargs):
            self.chamadas.append({"pi_id": pi_id, **kwargs})
            class _Fake:
                id = pi_id
                status = "canceled"
            return _Fake()
        stripe.PaymentIntent.cancel = fake_cancel
        return self

    def __exit__(self, *exc):
        stripe.PaymentIntent.cancel = self._original


def teste_cancela_pedido_preso_alem_do_prazo():
    Session = _montar_sessionmaker_sqlite()
    db_setup = Session()
    master = _pedido_authorized(db_setup, "01ORDER_PRESO", minutos_desde_expiracao=5)
    payment_intent_id_esperado = master.payment_intent_id  # ler antes de fechar a sessão
    db_setup.close()

    original_session_local = reconciliation.SessionLocal
    reconciliation.SessionLocal = Session
    try:
        with _StripeCancelMock() as m:
            asyncio.run(reconciliation._cancelar_autorizacoes_presas())
            assert len(m.chamadas) == 1
            assert m.chamadas[0]["pi_id"] == payment_intent_id_esperado
    finally:
        reconciliation.SessionLocal = original_session_local

    db_check = Session()
    atualizado = db_check.query(OrderDB).filter(OrderDB.gid == "01ORDER_PRESO").first()
    assert atualizado.payment_status == "CANCELED"
    assert atualizado.status == "Cancelado"
    sub = db_check.query(SubOrderDB).filter(SubOrderDB.master_order_gid == "01ORDER_PRESO").first()
    assert sub.status == "Cancelado"
    print("OK  - pedido AUTHORIZED preso além do prazo é cancelado, sub-pedido também")


def teste_nao_toca_pedido_ainda_dentro_do_prazo():
    Session = _montar_sessionmaker_sqlite()
    db_setup = Session()
    master = _pedido_authorized(db_setup, "01ORDER_NO_PRAZO", minutos_desde_expiracao=-30)
    db_setup.close()

    original_session_local = reconciliation.SessionLocal
    reconciliation.SessionLocal = Session
    try:
        with _StripeCancelMock() as m:
            asyncio.run(reconciliation._cancelar_autorizacoes_presas())
            assert len(m.chamadas) == 0
    finally:
        reconciliation.SessionLocal = original_session_local

    db_check = Session()
    atualizado = db_check.query(OrderDB).filter(OrderDB.gid == "01ORDER_NO_PRAZO").first()
    assert atualizado.payment_status == "AUTHORIZED"
    print("OK  - pedido ainda dentro do prazo de segurança não é tocado")


def teste_nao_toca_pedido_ja_capturado():
    """Independente do prazo, um pedido que já saiu de AUTHORIZED (foi capturado ou
    cancelado por outro caminho) não deve ser mexido por esta rotina."""
    Session = _montar_sessionmaker_sqlite()
    db_setup = Session()
    master = _pedido_authorized(db_setup, "01ORDER_CAPTURADO", minutos_desde_expiracao=5)
    master.payment_status = "CAPTURED"
    db_setup.commit()
    db_setup.close()

    original_session_local = reconciliation.SessionLocal
    reconciliation.SessionLocal = Session
    try:
        with _StripeCancelMock() as m:
            asyncio.run(reconciliation._cancelar_autorizacoes_presas())
            assert len(m.chamadas) == 0
    finally:
        reconciliation.SessionLocal = original_session_local
    print("OK  - pedido já capturado (payment_status != AUTHORIZED) não é afetado")


def teste_prazo_configurado_em_1_hora_por_padrao():
    from core.config import settings
    assert settings.PRAZO_SEGURANCA_AUTORIZACAO_MINUTOS == 60
    print("OK  - PRAZO_SEGURANCA_AUTORIZACAO_MINUTOS está configurado para 60 minutos (1h)")


if __name__ == "__main__":
    teste_prazo_configurado_em_1_hora_por_padrao()
    teste_cancela_pedido_preso_alem_do_prazo()
    teste_nao_toca_pedido_ainda_dentro_do_prazo()
    teste_nao_toca_pedido_ja_capturado()
    print("\nTodos os testes do cinto de segurança de autorização passaram.")
