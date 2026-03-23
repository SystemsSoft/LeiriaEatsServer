# Arquivo: services/courier_notification_service.py
"""
BackgroundTask que monitora pedidos ativos e notifica os estafetas
20 minutos antes do pedido estar pronto para recolha.

Lógica de tempo:
  - created_at       → momento em que o pedido foi criado (UTC)
  - base_time        → minutos estimados de preparação
  - ready_at         → created_at + base_time minutos
  - notify_at        → ready_at - 20 minutos

O worker acorda a cada 60 segundos, procura pedidos cujo
notify_at está a ≤ 0 segundos de distância (ou já passou há
menos de 5 minutos, para tolerância a reinícios) e que
ainda não foram notificados, e envia a notificação.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from core.database import SessionLocal
from core.sql_models import OrderDB

# ──────────────────────────────────────────
# Configuração
# ──────────────────────────────────────────
logger = logging.getLogger("courier_notification")

LISBON_TZ = ZoneInfo("Europe/Lisbon")   # timezone de Portugal (UTC+0 inverno / UTC+1 verão)

NOTIFY_BEFORE_MINUTES = 20   # avisar X minutos antes do pedido ficar pronto
POLL_INTERVAL_SECONDS = 60   # verifica a cada N segundos
TOLERANCE_MINUTES = 5        # janela de reenvio para pedidos que já passaram o notify_at

# Estados considerados "activos" (aguardam recolha)
ACTIVE_STATUSES = {"Pendente", "Em preparação"}

# ──────────────────────────────────────────
# Conjunto em memória de pedidos já notificados
# (evita duplicados enquanto o servidor está up)
# ──────────────────────────────────────────
_notified_order_ids: set[int] = set()


# ──────────────────────────────────────────
# Função de envio de notificação
# ──────────────────────────────────────────
def _send_courier_notification(order: OrderDB) -> None:
    """
    Ponto central de envio de notificação ao estafeta.
    Actualmente faz log estruturado; substitua pelo canal real
    (Firebase FCM, push notification, websocket, etc.).
    """
    ready_at_utc = _compute_ready_at(order)
    ready_at_lisbon = ready_at_utc.astimezone(LISBON_TZ)   # converte para hora de Portugal

    logger.info(
        "🚴 [COURIER NOTIFY] Pedido #%d | Restaurante: %s | Pronto às: %s (Lisboa) | "
        "Endereço de entrega: %s",
        order.id,
        order.restaurant_name,
        ready_at_lisbon.strftime("%H:%M"),
        order.delivery_address,
    )
    print(
        f"🚴 [COURIER NOTIFY] Pedido #{order.id} estará pronto às "
        f"{ready_at_lisbon.strftime('%H:%M')} (hora de Lisboa) — notificando estafeta!"
    )


# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────
def _compute_ready_at(order: OrderDB) -> datetime:
    """Devolve o datetime UTC em que o pedido estará pronto."""
    created = order.created_at
    # DateTime(timezone=True) já devolve aware; fallback para UTC se vier naive
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    else:
        # Normaliza para UTC puro (independente do tzinfo vir como +00:00 ou LISBON_TZ)
        created = created.astimezone(timezone.utc)
    return created + timedelta(minutes=order.base_time)


def _compute_notify_at(order: OrderDB) -> datetime:
    """Devolve o datetime UTC em que o estafeta deve ser avisado."""
    return _compute_ready_at(order) - timedelta(minutes=NOTIFY_BEFORE_MINUTES)


# ──────────────────────────────────────────
# Worker principal (loop assíncrono)
# ──────────────────────────────────────────
async def courier_notification_worker() -> None:
    """
    Loop infinito que corre em background (asyncio) e verifica
    periodicamente quais pedidos precisam de notificação.
    """
    logger.info("🟢 Courier notification worker iniciado.")
    print("🟢 Courier notification worker iniciado.")

    while True:
        try:
            _check_and_notify()
        except Exception as exc:
            logger.exception("❌ Erro no courier notification worker: %s", exc)

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def _check_and_notify() -> None:
    """Abre uma sessão de base de dados, verifica pedidos e notifica."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=TOLERANCE_MINUTES)

    db = SessionLocal()
    try:
        # Pedidos activos com base_time > 0 e ainda não notificados
        active_orders: list[OrderDB] = (
            db.query(OrderDB)
            .filter(OrderDB.status.in_(ACTIVE_STATUSES))
            .filter(OrderDB.base_time > 0)
            .all()
        )

        for order in active_orders:
            if order.id in _notified_order_ids:
                continue  # já notificado nesta sessão do servidor

            notify_at = _compute_notify_at(order)

            # Notifica se o momento de aviso está dentro da janela [now-tolerance, now]
            if window_start <= notify_at <= now:
                _send_courier_notification(order)
                _notified_order_ids.add(order.id)

    finally:
        db.close()

