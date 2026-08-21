# Arquivo: services/payment_reconciliation_service.py
import asyncio
import logging
import stripe
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from core.database import SessionLocal
from core.sql_models import OrderDB
from core import config

logger = logging.getLogger("payment_reconciliation")
stripe.api_key = config.settings.STRIPE_API_KEY

POLL_INTERVAL_SECONDS = 30  # Verifica a cada 30 segundos
MAX_ORDER_AGE_MINUTES = 60  # Apenas reconcilia pedidos criados na última hora

async def payment_reconciliation_worker():
    """
    Worker em background que verifica se pedidos PENDING_PAYMENT foram pagos na Stripe.
    Serve como redundância caso o Webhook falhe.
    """
    logger.info("🟢 Payment reconciliation worker iniciado.")
    print("🟢 Payment reconciliation worker iniciado.")

    while True:
        try:
            await _reconcile_pending_payments()
        except Exception as e:
            logger.error(f"❌ Erro no reconciliation worker: {e}")
            print(f"❌ [RECONCILIATION] Erro: {e}")
        
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

async def _reconcile_pending_payments():
    """Busca pedidos pendentes e valida status na Stripe"""
    db = SessionLocal()
    try:
        # Apenas pedidos PENDING_PAYMENT que tenham um payment_intent_id
        # e que não sejam muito antigos para não sobrecarregar a API
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=MAX_ORDER_AGE_MINUTES)
        
        pending_orders = db.query(OrderDB).filter(
            OrderDB.status == "PENDING_PAYMENT",
            OrderDB.payment_intent_id.isnot(None),
            OrderDB.created_at >= cutoff
        ).all()

        if not pending_orders:
            return

        for order in pending_orders:
            try:
                # Consultar Stripe diretamente
                pi = stripe.PaymentIntent.retrieve(order.payment_intent_id)
                
                if pi.status == "succeeded":
                    print(f"💰 [RECONCILIATION] Sucesso detectado para Pedido #{order.id} (PI: {order.payment_intent_id})")
                    
                    # Atualizar Master Order
                    order.status = "Pendente"
                    
                    # Propagar para Sub-Orders
                    for sub in order.sub_orders:
                        sub.status = "Pendente"
                        
                    db.commit()
                    print(f"✅ [RECONCILIATION] Pedido #{order.id} sincronizado para 'Pendente'.")
                
                elif pi.status == "canceled":
                    order.status = "Cancelado"
                    for sub in order.sub_orders:
                        sub.status = "Cancelado"
                    db.commit()
                    print(f"🚫 [RECONCILIATION] Pedido #{order.id} cancelado na Stripe. Sincronizado.")

            except stripe.error.StripeError as e:
                logger.warning(f"⚠️ Erro ao consultar Stripe para Pedido #{order.id}: {e}")
            except Exception as e:
                logger.error(f"❌ Erro ao processar Pedido #{order.id}: {e}")

    finally:
        db.close()
