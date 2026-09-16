# Arquivo: services/courier_notification_service.py
import asyncio
import logging
import math
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import func
from sqlalchemy.exc import OperationalError
from ulid import ULID
from core.database import SessionLocal
from core.config import settings
from core.sql_models import OrderDB, DriverDB, SubOrderDB, DeliveryRouteDB, RouteStopDB
from services.route_sequencer import Point, Stop, sequence_stops, travel_minutes
from services.transit_tolerance_service import tolerancia_padrao_por_categoria
from services import push_notification_service

logger = logging.getLogger("courier_notification")
LISBON_TZ = ZoneInfo("Europe/Lisbon")

POLL_INTERVAL_SECONDS = 60
DRIVER_ONLINE_MINUTES = 2
ACCEPT_TIMEOUT_SECONDS = 60
# PLANO_RECOLHA_MULTI_RESTAURANTE.md, Fase 3 — margem de segurança entre o instante em
# que o estafeta teoricamente chegaria à 1ª paragem e o instante em que a oferta é
# enviada. Substitui o antigo NOTIFY_BEFORE_MINUTES fixo: agora o "quando oferecer" é
# calculado por rota (min(ready_at) − viagem(estafeta→1ª paragem) − buffer, secção 4).
OFFER_BUFFER_MINUTES = 5

ACTIVE_STATUSES = {"Em preparo"}
# PLANO_RECOLHA_MULTI_RESTAURANTE.md, Fase 0.2: o app do restaurante grava "Em Preparo"
# (P maiúsculo) — a query abaixo usada compara em minúsculas pra não depender de as duas
# pontas escreverem a mesma grafia. Confirmado em produção: comparação exata (BINARY)
# retornava 0 sub-pedidos elegíveis para despacho, mesmo com sub-pedidos "Em Preparo" reais.
_ACTIVE_STATUSES_LOWER = {s.lower() for s in ACTIVE_STATUSES}

ACTIVE_ROUTE_STATUSES = ("OFFERED", "ACCEPTED", "IN_PROGRESS")

# Estado em memória do MECANISMO ANTIGO (despacho 1:1, pré-Fase 3) — mantido só para não
# quebrar clear_dispatch_state()/reset_order_delivery (Fase 0), que continuam a existir
# para o fluxo manual de "reiniciar busca" por sub-pedido isolado. O despacho automático
# de verdade passa a viver em DeliveryRouteDB (Fase 3, item 4 do plano: estado em banco,
# sobrevive a restart e a mais de um processo).
_notified_sub_order_ids: set[int] = set()
_pending_acceptance: dict[int, datetime] = {}


def clear_dispatch_state(sub_order_id: int) -> None:
    """
    Ver nota acima — mecanismo antigo, mantido por compatibilidade com order_routes.py
    (reset_order_delivery) e api/routes/drivers.py (reject_order da Fase 0). Não-op na
    prática desde a Fase 3, já que o worker novo não popula mais estes dicionários.
    """
    _notified_sub_order_ids.discard(sub_order_id)
    _pending_acceptance.pop(sub_order_id, None)


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _calculate_delivery_fee(total_distance_km: float) -> float:
    """Mesma fórmula de api/routes/drivers.py::_calculate_delivery_fee — duplicada (não
    importada) para não criar import circular (drivers.py já importa deste módulo)."""
    fee = 1.20 + (total_distance_km * 0.35)
    return max(2.50, round(fee, 2))


def _compute_ready_at(sub_order: SubOrderDB) -> datetime:
    # PLANO_RECOLHA_MULTI_RESTAURANTE.md, Fase 2 — quando o restaurante já confirmou
    # prontidão real (botão "Pedido pronto"), esse timestamp é mais confiável que a
    # estimativa por base_time e tem prioridade sobre ela.
    if sub_order.ready_at is not None:
        ready_at = sub_order.ready_at
        return ready_at if ready_at.tzinfo is not None else ready_at.replace(tzinfo=timezone.utc)

    created = sub_order.master_order.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    else:
        created = created.astimezone(timezone.utc)
    return created + timedelta(minutes=sub_order.base_time)


# ─── Fase 3 — despacho por rota agrupada (Opção B, secção 3 do plano) ──────────────────

def _busy_driver_gids_subquery(db):
    """Estafetas com uma rota ainda ativa — substitui o antigo filtro por sub-pedido
    (courier_notification_service.py:50, versão pré-Fase 3), que impedia o mesmo estafeta
    de pegar dois restaurantes do MESMO pedido em vez de bloquear só quando ele já tem
    outra rota."""
    return db.query(DeliveryRouteDB.driver_gid).filter(
        DeliveryRouteDB.driver_gid.isnot(None),
        DeliveryRouteDB.status.in_(ACTIVE_ROUTE_STATUSES),
    )


def _eligible_subs_grouped_by_master(db) -> dict[str, list[SubOrderDB]]:
    busy_master_gids = db.query(DeliveryRouteDB.master_order_gid).filter(
        DeliveryRouteDB.status.in_(ACTIVE_ROUTE_STATUSES)
    )
    rows = (
        db.query(SubOrderDB)
        .join(OrderDB)
        .filter(func.lower(SubOrderDB.status).in_(_ACTIVE_STATUSES_LOWER))
        .filter(SubOrderDB.base_time > 0)
        .filter(SubOrderDB.gid.isnot(None))
        .filter(OrderDB.delivery_type != "pickup")
        .filter(SubOrderDB.master_order_gid.notin_(busy_master_gids))
        .order_by(SubOrderDB.master_order_gid, SubOrderDB.id)
        .all()
    )
    grouped: dict[str, list[SubOrderDB]] = {}
    for sub in rows:
        grouped.setdefault(sub.master_order_gid, []).append(sub)
    return grouped


def _build_stops(subs: list[SubOrderDB]) -> list[Stop] | None:
    stops = []
    for sub in subs:
        if sub.restaurant_latitude is None or sub.restaurant_longitude is None:
            logger.warning(f"⚠️ Sub-Pedido #{sub.id} — restaurante sem GPS, rota adiada.")
            return None
        # PLANO_RECOLHA_MULTI_RESTAURANTE.md, secção 4.4, nota de dependência: derivar a
        # tolerância dos ITENS do pedido exige product_gid em OrderItemDB (secção 5.4),
        # ainda não implementado. Até lá, resolve-se pela categoria do restaurante
        # (SubOrderDB.restaurant_category), já desnormalizada — menos preciso, mas
        # suficiente para arrancar sem migração adicional.
        tolerance = tolerancia_padrao_por_categoria(sub.restaurant_category)
        stops.append(Stop(
            stop_id=str(sub.id),
            location=Point(sub.restaurant_latitude, sub.restaurant_longitude),
            ready_at=_compute_ready_at(sub),
            tolerance_minutes=float(tolerance),
        ))
    return stops


def _centroid(stops: list[Stop]) -> Point:
    lat = sum(s.location.latitude for s in stops) / len(stops)
    lon = sum(s.location.longitude for s in stops) / len(stops)
    return Point(lat, lon)


def _nearest_available_driver(db, reference: Point, busy_gids_subquery) -> DriverDB | None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=DRIVER_ONLINE_MINUTES)
    candidates = db.query(DriverDB).filter(
        DriverDB.status == "ACTIVE",
        DriverDB.last_seen >= cutoff,
        DriverDB.latitude.isnot(None),
        DriverDB.longitude.isnot(None),
        DriverDB.gid.notin_(busy_gids_subquery),
    ).all()
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda d: _haversine(reference.latitude, reference.longitude, d.latitude, d.longitude),
    )


def _try_offer_route(db, master_order_gid: str, subs: list[SubOrderDB], now: datetime) -> None:
    # Teto herdado do limite já existente na criação do pedido (PLANO_LIMITE_RESTAURANTES.md).
    # O plano (secção 4.3) recomenda arrancar com 2 paragens por rota e só subir para 3 com
    # dados reais de atraso — decisão de calibração ainda não tomada aqui; manter o teto do
    # pedido evita órfãos (um 3º restaurante que nunca seria despachado).
    max_stops = max(1, settings.MAX_RESTAURANTES_POR_PEDIDO)
    subs = subs[:max_stops]

    stops = _build_stops(subs)
    if not stops:
        return

    master_order = subs[0].master_order
    if master_order.delivery_latitude is None or master_order.delivery_longitude is None:
        logger.warning(f"⚠️ Pedido {master_order_gid} sem coordenadas de entrega — rota adiada.")
        return
    customer_location = Point(master_order.delivery_latitude, master_order.delivery_longitude)

    driver = _nearest_available_driver(db, _centroid(stops), _busy_driver_gids_subquery(db))
    if not driver:
        return

    driver_location = Point(driver.latitude, driver.longitude)
    result = sequence_stops(stops, driver_location, customer_location, now=now)

    first_timing = result.stop_timings[0]
    first_stop = next(s for s in stops if s.stop_id == first_timing.stop_id)
    travel_to_first = travel_minutes(driver_location, first_stop.location)
    # Secção 4: momento da oferta = min(ready_at) − viagem(estafeta→1ª paragem) − buffer.
    # first_timing.pickup já é max(chegada_estimada, ready_at) da 1ª paragem escolhida —
    # generaliza a fórmula do plano sem repetir o cálculo.
    notify_at = first_timing.pickup - timedelta(minutes=travel_to_first) - timedelta(minutes=OFFER_BUFFER_MINUTES)
    if notify_at > now:
        return  # ainda não é a hora de oferecer esta rota

    # Distância total da rota (driver -> paragens na ordem escolhida -> cliente), em linha
    # reta — mesma convenção (sem fator de estrada) do cálculo antigo de taxa por
    # sub-pedido único, para não mudar a escala de preço já em uso.
    ordered_locations = [driver_location]
    for stop_id in result.order:
        ordered_locations.append(next(s for s in stops if s.stop_id == stop_id).location)
    ordered_locations.append(customer_location)
    total_distance_km = sum(
        _haversine(a.latitude, a.longitude, b.latitude, b.longitude)
        for a, b in zip(ordered_locations, ordered_locations[1:])
    )
    estimated_fee = _calculate_delivery_fee(total_distance_km)

    subs_by_id = {str(sub.id): sub for sub in subs}
    route = DeliveryRouteDB(
        gid=str(ULID()),
        master_order_gid=master_order_gid,
        driver_gid=driver.gid,
        status="OFFERED",
        offered_at=now,
        estimated_delivery_at=result.delivery_at,
        estimated_fee=estimated_fee,
        sequence_version=1,
    )
    db.add(route)

    for sequence, stop_id in enumerate(result.order, start=1):
        sub = subs_by_id[stop_id]
        db.add(RouteStopDB(
            route_gid=route.gid,
            sub_order_gid=sub.gid,
            sequence=sequence,
            ready_at_estimated=_compute_ready_at(sub),
            ready_at_confirmed=sub.ready_at,
        ))
        sub.status = "Oferta enviada"
        # Espelho só de leitura para o KomaRestaurant (secção 5.3) — a atribuição real
        # (driver_gid) vive só na rota, não no sub-pedido, para não ambiguar com os
        # endpoints antigos de aceite/recusa por sub-pedido (api/routes/drivers.py).
        sub.driver_name = driver.name

    db.commit()
    logger.info(
        f"📨 Rota {route.gid} oferecida a {driver.name} — {len(stops)} paragem(ns), "
        f"entrega estimada às {result.delivery_at.astimezone(LISBON_TZ).strftime('%H:%M')}."
    )
    # Fase 5 — melhor esforço: nunca deixa uma falha de push impedir o despacho, que já
    # está persistido e servido via GET /drivers/routes independentemente disto.
    try:
        push_notification_service.send_route_offer(driver, route)
    except Exception as exc:
        logger.warning(f"⚠️ send_route_offer falhou para a rota {route.gid}: {exc}")


def _expire_pending_routes(db, now: datetime) -> None:
    offered = db.query(DeliveryRouteDB).filter(DeliveryRouteDB.status == "OFFERED").all()
    expired_any = False
    for route in offered:
        offered_at = route.offered_at
        if offered_at is None:
            continue
        if offered_at.tzinfo is None:
            offered_at = offered_at.replace(tzinfo=timezone.utc)
        if (now - offered_at).total_seconds() <= ACCEPT_TIMEOUT_SECONDS:
            continue

        route.status = "EXPIRED"
        route.driver_gid = None
        for stop in route.stops:
            sub = stop.sub_order
            if sub and sub.status == "Oferta enviada":
                sub.status = "Em preparo"
                sub.driver_name = None
        expired_any = True
        logger.info(f"⌛ Rota {route.gid} expirou sem aceite — sub-pedidos devolvidos ao pool.")

    if expired_any:
        db.commit()


async def courier_notification_worker() -> None:
    logger.info("🟢 Courier notification worker iniciado (despacho por rota — Fase 3).")
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
        _expire_pending_routes(db, now)

        grouped = _eligible_subs_grouped_by_master(db)
        for master_order_gid, subs in grouped.items():
            _try_offer_route(db, master_order_gid, subs, now)
    finally:
        db.close()
