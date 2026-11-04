# Arquivo: services/driver_payout_service.py
"""
Pagamento do estafeta por rota concluída (app KomaPartner).

O cliente paga a taxa de entrega à plataforma (os restaurantes só a recebem quando têm
estafeta próprio — ver _repassar_para_restaurantes em order_routes.py). Ao concluir a
rota, a plataforma transfere ao estafeta o `estimated_fee` mostrado no app antes de ele
aceitar a oferta.

Sem migração: usa as colunas já existentes em SubOrderDB (`driver_delivery_fee`,
`driver_payment_transfer_id`), repartindo a taxa pelos sub-pedidos entregues da rota —
todos guardam o id da mesma transferência.

Idempotência em duas camadas, como no repasse aos restaurantes: idempotency_key no
Stripe + checagem de driver_payment_transfer_id. Nunca lança exceção: uma falha deixa o
pagamento pendente e o worker de despacho volta a tentar (retry_pending_payouts).
"""
import logging
from datetime import datetime, timedelta, timezone

import stripe

from core.sql_models import DeliveryRouteDB, DriverDB

logger = logging.getLogger("driver_payout")

# Rotas concluídas há mais do que isto deixam de ser retentadas automaticamente.
RETRY_WINDOW_DAYS = 7


def _paid_subs(route: DeliveryRouteDB) -> list:
    return [
        stop.sub_order for stop in route.stops
        if stop.sub_order is not None and (stop.sub_order.status or "").lower() == "entregue"
    ]


def is_route_paid(route: DeliveryRouteDB) -> bool:
    subs = _paid_subs(route)
    return bool(subs) and all(s.driver_payment_transfer_id for s in subs)


def _split_fee(total: float, parts: int) -> list[float]:
    """Reparte em cêntimos; o resto vai para o primeiro sub-pedido."""
    total_cents = int(round(total * 100))
    base = total_cents // parts
    shares = [base] * parts
    shares[0] += total_cents - base * parts
    return [c / 100 for c in shares]


def pay_driver_for_route(db, route: DeliveryRouteDB) -> str | None:
    """Transfere a taxa da rota ao estafeta. Devolve o id da transferência ou None."""
    if route.status != "COMPLETED" or is_route_paid(route):
        return None

    subs = _paid_subs(route)
    amount = route.estimated_fee or 0.0
    amount_cents = int(round(amount * 100))
    if not subs or amount_cents <= 0:
        return None

    driver = db.query(DriverDB).filter(DriverDB.gid == route.driver_gid).first()
    if not driver or not driver.stripe_account_id or not driver.stripe_onboarding_completed:
        logger.warning(f"⚠️ [Estafeta] Rota {route.gid}: estafeta sem conta Stripe apta — pagamento pendente.")
        return None

    # Só paga com o pagamento do cliente capturado — e ligado a ele (source_transaction),
    # nunca a partir do saldo geral da plataforma.
    master = route.master_order
    if master is None or not master.payment_intent_id:
        logger.warning(f"⚠️ [Estafeta] Rota {route.gid}: pedido sem pagamento Stripe — pagamento pendente.")
        return None
    try:
        pi = stripe.PaymentIntent.retrieve(master.payment_intent_id)
    except stripe.error.StripeError as e:
        logger.warning(f"⚠️ [Estafeta] Rota {route.gid}: não foi possível ler o pagamento — {e}")
        return None
    if pi.get("status") != "succeeded" or not pi.get("latest_charge"):
        logger.info(f"ℹ️ [Estafeta] Rota {route.gid}: pagamento do pedido ainda não capturado — pendente.")
        return None

    try:
        transfer = stripe.Transfer.create(
            amount=amount_cents,
            currency="eur",
            destination=driver.stripe_account_id,
            source_transaction=pi.get("latest_charge"),
            metadata={
                "route_gid": route.gid,
                "master_order_gid": route.master_order_gid,
                "driver_gid": driver.gid,
            },
            idempotency_key=f"transfer_route_{route.gid}",
        )
    except stripe.error.StripeError as e:
        logger.warning(f"❌ [Estafeta] Rota {route.gid}: falha ao transferir — {e}")
        return None

    for sub, share in zip(subs, _split_fee(amount, len(subs))):
        sub.driver_delivery_fee = share
        sub.driver_payment_transfer_id = transfer.id
    db.commit()
    logger.info(f"✅ [Estafeta] Rota {route.gid} → {driver.name}: €{amount:.2f} (transfer {transfer.id})")
    return transfer.id


def retry_pending_payouts(db, now: datetime | None = None) -> None:
    """Chamado pelo worker de despacho: volta a tentar pagamentos que ficaram pendentes."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=RETRY_WINDOW_DAYS)
    routes = (
        db.query(DeliveryRouteDB)
        .filter(DeliveryRouteDB.status == "COMPLETED")
        .filter(DeliveryRouteDB.completed_at >= since)
        .all()
    )
    for route in routes:
        if not is_route_paid(route):
            pay_driver_for_route(db, route)
