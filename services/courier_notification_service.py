# Arquivo: services/courier_notification_service.py
"""
Worker em background que monitoriza pedidos activos e:
  1. Calcula o momento de notificação: ready_at - NOTIFY_BEFORE_MINUTES
  2. Quando esse momento chega, encontra o estafeta ACTIVE mais próximo
     do restaurante (usando a última posição conhecida via polling GPS).
  3. Envia oferta ao estafeta → status 'Oferta enviada'.
  4. O estafeta aceita  → status 'A aguardar estafeta'.
     O estafeta recusa / timeout → limpa atribuição e tenta próximo.

Timing:
  ready_at   = created_at + base_time (minutos)
  notify_at  = ready_at  - NOTIFY_BEFORE_MINUTES
"""

import asyncio
import logging
import math
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.exc import OperationalError

from core.database import SessionLocal
from core.sql_models import OrderDB, DriverDB

# ──────────────────────────────────────────
# Configuração
# ──────────────────────────────────────────
logger = logging.getLogger("courier_notification")

LISBON_TZ = ZoneInfo("Europe/Lisbon")

NOTIFY_BEFORE_MINUTES = 25   # avisar X minutos antes do pedido ficar pronto
POLL_INTERVAL_SECONDS = 60   # verifica a cada N segundos
TOLERANCE_MINUTES     = 5    # janela de reenvio para pedidos que já passaram o notify_at
DRIVER_ONLINE_MINUTES = 2    # considera driver "online" se last_seen < N minutos atrás
ACCEPT_TIMEOUT_SECONDS = 60  # estafeta tem X segundos para aceitar ou recusar a oferta

# Estados considerados "activos" (aguardam atribuição de estafeta)
# Apenas "Em preparo": o restaurante já confirmou e está a preparar — só então faz sentido chamar um estafeta
ACTIVE_STATUSES = {"Em preparo"}

# Conjunto em memória de pedidos já notificados
_notified_order_ids: set[int] = set()

# Dicionário em memória: order_id → datetime em que a oferta foi enviada ao estafeta
_pending_acceptance: dict[int, datetime] = {}


# ──────────────────────────────────────────
# Haversine — distância entre dois pontos GPS
# ──────────────────────────────────────────
def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância em km entre dois pontos geográficos (fórmula de Haversine)."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ──────────────────────────────────────────
# Atribuição do estafeta mais próximo
# ──────────────────────────────────────────
def _assign_nearest_driver(order: OrderDB, db) -> DriverDB | None:
    """
    Encontra o estafeta ACTIVE mais próximo do restaurante do pedido,
    atribui-o ao pedido e muda o status para 'Oferta enviada'.
    O estafeta tem ACCEPT_TIMEOUT_SECONDS para aceitar ou recusar.
    Devolve o DriverDB atribuído, ou None se não houver nenhum disponível.
    """
    restaurant = order.restaurant
    if not restaurant or restaurant.latitude is None or restaurant.longitude is None:
        logger.warning(
            "⚠️  Pedido #%d — restaurante sem coordenadas GPS, não é possível atribuir estafeta.",
            order.id,
        )
        return None

    # Guarda defensiva: nunca sobrescrever uma atribuição já existente
    if order.driver_id is not None:
        logger.info(
            "ℹ️  Pedido #%d já tem estafeta atribuído (id=%d), a saltar.",
            order.id, order.driver_id,
        )
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=DRIVER_ONLINE_MINUTES)

    # IDs dos estafetas que já têm uma viagem activa (oferta pendente, a aguardar recolha ou já a caminho)
    busy_driver_ids = db.query(OrderDB.driver_id).filter(
        OrderDB.driver_id.isnot(None),
        OrderDB.status.in_(["Oferta enviada", "A aguardar estafeta", "A caminho"]),
    )

    candidates = db.query(DriverDB).filter(
        DriverDB.status    == "ACTIVE",
        DriverDB.last_seen >= cutoff,
        DriverDB.latitude.isnot(None),
        DriverDB.longitude.isnot(None),
        DriverDB.id.notin_(busy_driver_ids),   # exclui estafetas já ocupados
    ).all()

    if not candidates:
        logger.warning(
            "⚠️  Pedido #%d — nenhum estafeta livre online nos últimos %d minutos.",
            order.id, DRIVER_ONLINE_MINUTES,
        )
        return None

    nearest = min(
        candidates,
        key=lambda d: _haversine(restaurant.latitude, restaurant.longitude, d.latitude, d.longitude),
    )
    dist_km = _haversine(restaurant.latitude, restaurant.longitude, nearest.latitude, nearest.longitude)

    # Persiste a oferta no pedido — estafeta ainda não aceitou
    order.driver_id   = nearest.id
    order.driver_name = nearest.name
    order.status      = "Oferta enviada"
    db.commit()

    # Regista o momento da oferta para controlo de timeout
    _pending_acceptance[order.id] = datetime.now(timezone.utc)

    logger.info(
        "📨 Oferta enviada → estafeta id=%d (%s) a %.2f km do restaurante '%s' | Pedido #%d.",
        nearest.id, nearest.name, dist_km, restaurant.name, order.id,
    )
    print(
        f"📨 Oferta enviada → estafeta '{nearest.name}' (id={nearest.id}) "
        f"a {dist_km:.2f} km | Pedido #{order.id} | timeout: {ACCEPT_TIMEOUT_SECONDS}s"
    )
    return nearest


# ──────────────────────────────────────────
# Função de envio de notificação
# ──────────────────────────────────────────
def _send_courier_notification(order: OrderDB, driver: DriverDB) -> None:
    """
    Ponto central de envio de notificação ao estafeta.
    Substitua pelo canal real (Firebase FCM, push notification, etc.).
    """
    ready_at_lisbon = _compute_ready_at(order).astimezone(LISBON_TZ)
    logger.info(
        "🔔 [COURIER NOTIFY] Pedido #%d | Restaurante: %s | Estafeta: %s | "
        "Pronto às: %s (Lisboa) | Endereço de entrega: %s",
        order.id, order.restaurant_name, driver.name,
        ready_at_lisbon.strftime("%H:%M"), order.delivery_address,
    )
    print(
        f"🔔 [COURIER NOTIFY] Pedido #{order.id} → estafeta '{driver.name}' | "
        f"pronto às {ready_at_lisbon.strftime('%H:%M')} (Lisboa) | "
        f"entregar em: {order.delivery_address}"
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
        except OperationalError as exc:
            logger.error(
                "🔌 Falha de conexão com a base de dados no worker — será retentado no próximo ciclo (%ds). Detalhe: %s",
                POLL_INTERVAL_SECONDS, exc,
            )
            print(f"🔌 [WORKER] Timeout/erro de BD — a retomar em {POLL_INTERVAL_SECONDS}s.")
        except Exception as exc:
            logger.exception("❌ Erro no courier notification worker: %s", exc)

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def _check_and_notify() -> None:
    """Abre uma sessão de BD, verifica pedidos e atribui estafetas."""
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        # ── 1. Verificar timeouts de aceitação ──────────────────────────────
        expired_offers = [
            oid for oid, sent_at in list(_pending_acceptance.items())
            if (now - sent_at).total_seconds() > ACCEPT_TIMEOUT_SECONDS
        ]
        for order_id in expired_offers:
            order = db.query(OrderDB).filter(OrderDB.id == order_id).first()
            if order and order.status == "Oferta enviada":
                print(
                    f"⏰ Pedido #{order_id} — timeout de aceitação ({ACCEPT_TIMEOUT_SECONDS}s), "
                    f"estafeta '{order.driver_name}' não respondeu. A tentar próximo estafeta."
                )
                logger.warning(
                    "⏰ Pedido #%d — timeout, estafeta id=%d não aceitou. A re-atribuir.",
                    order_id, order.driver_id,
                )
                # Limpa a atribuição → o worker pode tentar outro estafeta
                order.driver_id   = None
                order.driver_name = None
                order.status      = "Em preparo"
                db.commit()

            # Remove do tracker independentemente
            _pending_acceptance.pop(order_id, None)
            _notified_order_ids.discard(order_id)

        # ── 2. Processar pedidos activos sem estafeta ────────────────────────
        active_orders: list[OrderDB] = (
            db.query(OrderDB)
            .filter(OrderDB.status.in_(ACTIVE_STATUSES))
            .filter(OrderDB.base_time > 0)
            .filter(OrderDB.driver_id.is_(None))
            .filter(OrderDB.delivery_type != "pickup")   # pedidos pickup não precisam de estafeta
            .all()
        )

        for order in active_orders:
            if order.id in _notified_order_ids:
                continue

            notify_at = _compute_notify_at(order)

            if notify_at <= now:
                # 1. Envia oferta ao estafeta mais próximo → status "Oferta enviada"
                driver = _assign_nearest_driver(order, db)

                if driver:
                    # 2. Envia notificação (Firebase FCM ou outro canal)
                    _send_courier_notification(order, driver)
                    _notified_order_ids.add(order.id)
                else:
                    # Sem estafeta disponível — tenta de novo no próximo ciclo (60s)
                    print(
                        f"⚠️  Pedido #{order.id} sem estafeta disponível — "
                        f"será tentado novamente no próximo ciclo."
                    )

    except Exception as exc:
        logger.exception("❌ Erro em _check_and_notify: %s", exc)
    finally:
        db.close()
