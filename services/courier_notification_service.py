# Arquivo: services/courier_notification_service.py
import asyncio
import logging
import math
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.exc import OperationalError
from core.database import SessionLocal
from core.sql_models import OrderDB, DriverDB, SubOrderDB

logger = logging.getLogger("courier_notification")
LISBON_TZ = ZoneInfo("Europe/Lisbon")

NOTIFY_BEFORE_MINUTES = 15
POLL_INTERVAL_SECONDS = 60
DRIVER_ONLINE_MINUTES = 2
ACCEPT_TIMEOUT_SECONDS = 60
ACTIVE_STATUSES = {"Em preparo"}

_notified_sub_order_ids: set[int] = set()
_pending_acceptance: dict[int, datetime] = {}

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def _assign_nearest_driver(sub_order: SubOrderDB, db) -> DriverDB | None:
    if sub_order.restaurant_latitude is None or sub_order.restaurant_longitude is None:
        logger.warning(f"⚠️ Sub-Pedido #{sub_order.id} — restaurante sem GPS.")
        return None

    if sub_order.driver_id is not None:
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=DRIVER_ONLINE_MINUTES)
    busy_driver_ids = db.query(SubOrderDB.driver_id).filter(
        SubOrderDB.driver_id.isnot(None),
        SubOrderDB.status.in_(["Oferta enviada", "A aguardar estafeta", "A caminho"]),
    )

    candidates = db.query(DriverDB).filter(
        DriverDB.status == "ACTIVE",
        DriverDB.last_seen >= cutoff,
        DriverDB.latitude.isnot(None),
        DriverDB.longitude.isnot(None),
        DriverDB.id.notin_(busy_driver_ids),
    ).all()

    if not candidates:
        return None

    nearest = min(
        candidates,
        key=lambda d: _haversine(sub_order.restaurant_latitude, sub_order.restaurant_longitude, d.latitude, d.longitude),
    )
    
    sub_order.driver_id = nearest.id
    sub_order.driver_name = nearest.name
    sub_order.status = "Oferta enviada"
    db.commit()

    _pending_acceptance[sub_order.id] = datetime.now(timezone.utc)
    logger.info(f"📨 Oferta enviada -> estafeta {nearest.name} para Sub-Pedido #{sub_order.id}")
    return nearest

def _send_courier_notification(sub_order: SubOrderDB, driver: DriverDB) -> None:
    ready_at = _compute_ready_at(sub_order).astimezone(LISBON_TZ)
    logger.info(f"🔔 [COURIER] Sub-Pedido #{sub_order.id} | Pronto: {ready_at.strftime('%H:%M')}")

def _compute_ready_at(sub_order: SubOrderDB) -> datetime:
    created = sub_order.master_order.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    else:
        created = created.astimezone(timezone.utc)
    return created + timedelta(minutes=sub_order.base_time)

def _compute_notify_at(sub_order: SubOrderDB) -> datetime:
    return _compute_ready_at(sub_order) - timedelta(minutes=NOTIFY_BEFORE_MINUTES)

async def courier_notification_worker() -> None:
    logger.info("🟢 Courier notification worker iniciado.")
    while True:
        try:
            _check_and_notify()
        except OperationalError:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except Exception as exc:
            logger.exception("❌ Erro no worker: %s", exc)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

def _check_and_notify() -> None:
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        expired_offers = [
            sid for sid, sent_at in list(_pending_acceptance.items())
            if (now - sent_at).total_seconds() > ACCEPT_TIMEOUT_SECONDS
        ]
        for sub_id in expired_offers:
            sub = db.query(SubOrderDB).filter(SubOrderDB.id == sub_id).first()
            if sub and sub.status == "Oferta enviada":
                sub.driver_id = None
                sub.driver_name = None
                sub.status = "Em preparo"
                db.commit()
            _pending_acceptance.pop(sub_id, None)
            _notified_sub_order_ids.discard(sub_id)

        active_subs = (
            db.query(SubOrderDB)
            .join(OrderDB)
            .filter(SubOrderDB.status.in_(ACTIVE_STATUSES))
            .filter(SubOrderDB.base_time > 0)
            .filter(SubOrderDB.driver_id.is_(None))
            .filter(OrderDB.delivery_type != "pickup")
            .all()
        )

        for sub in active_subs:
            if sub.id in _notified_sub_order_ids:
                continue
            if _compute_notify_at(sub) <= now:
                driver = _assign_nearest_driver(sub, db)
                if driver:
                    _send_courier_notification(sub, driver)
                    _notified_sub_order_ids.add(sub.id)
    finally:
        db.close()
