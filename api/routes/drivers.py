# Arquivo: api/routes/drivers.py
import hashlib
import math
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from starlette import status
from ulid import ULID

from core import config
from core.database import get_db
from core.sql_models import DriverDB, OrderDB, RestaurantDB, SubOrderDB, DeliveryRouteDB, RouteStopDB
from services.courier_notification_service import clear_dispatch_state
from schemas.driver import (
    DriverRegisterRequest,
    DriverLoginRequest,
    DriverLoginResponse,
    DriverLocationUpdate,
    UpdateDriverProfileRequest,
    DriverProfileResponse,
)

router = APIRouter(prefix="/drivers", tags=["Estafetas"])
stripe.api_key = config.settings.STRIPE_API_KEY

def _hash_password(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()

def _verify_password(plain: str, hashed: str) -> bool:
    return _hash_password(plain) == hashed

def _get_driver_or_404(driver_id: int, db: Session) -> DriverDB:
    driver = db.query(DriverDB).filter(DriverDB.id == driver_id).first()
    if not driver:
        raise HTTPException(status_code=404, detail="Estafeta não encontrado.")
    return driver

def _haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def _calculate_delivery_fee(total_distance_km):
    fee = 1.20 + (total_distance_km * 0.35)
    return max(2.50, round(fee, 2))

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_driver(payload: DriverRegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(DriverDB).filter(DriverDB.login == payload.login).first()
    if existing: raise HTTPException(status_code=400, detail="Login já existe.")
    driver = DriverDB(gid=str(ULID()), login=payload.login, password=_hash_password(payload.password), status="PENDING")
    db.add(driver)
    db.commit()
    db.refresh(driver)
    return {
        "gid": driver.gid,
        "status": driver.status,
        "profile_complete": False,
        "message": "Registado. Complete o onboarding Stripe.",
    }


@router.post("/login", response_model=DriverLoginResponse)
def login_driver(payload: DriverLoginRequest, db: Session = Depends(get_db)):
    driver = db.query(DriverDB).filter(DriverDB.login == payload.login).first()
    if not driver or not _verify_password(payload.password, driver.password):
        raise HTTPException(status_code=401, detail="Login ou senha inválidos.")

    return DriverLoginResponse(
        authenticated=True,
        gid=driver.gid,
        name=driver.name,
        status=driver.status,
        profile_complete=bool(driver.name and driver.vehicle_type),
        message="Login efetuado com sucesso.",
    )


def _get_driver_by_gid_or_404(gid: str, db: Session) -> DriverDB:
    """Busca o estafeta pelo gid (identidade pública/estável usada pelo app)."""
    driver = db.query(DriverDB).filter(DriverDB.gid == gid).first()
    if not driver and gid.isdigit():
        # Fallback: apps antigos ainda podem enviar o id numérico
        driver = db.query(DriverDB).filter(DriverDB.id == int(gid)).first()
    if not driver:
        raise HTTPException(status_code=404, detail="Estafeta não encontrado.")
    return driver


def _stripe_account_for(driver: DriverDB) -> str:
    """Garante que o estafeta tem uma conta Stripe Connect Express e devolve o id."""
    if driver.stripe_account_id:
        return driver.stripe_account_id

    stripe_email = driver.login if "@" in driver.login else f"{driver.login}@leiriaeats.com"
    account = stripe.Account.create(
        type="express",
        country="PT",
        email=stripe_email,
        capabilities={
            "card_payments": {"requested": True},
            "transfers": {"requested": True},
        },
    )
    driver.stripe_account_id = account.id
    driver.status = "STRIPE_PENDING"
    return driver.stripe_account_id


@router.post("/{gid}/stripe-onboarding")
def create_driver_stripe_onboarding(gid: str, db: Session = Depends(get_db)):
    """
    POST /drivers/{gid}/stripe-onboarding
    Obtém (ou renova) o link de onboarding do Stripe Connect para o estafeta.
    """
    driver = _get_driver_by_gid_or_404(gid, db)
    try:
        _stripe_account_for(driver)
        db.commit()

        account_link = stripe.AccountLink.create(
            account=driver.stripe_account_id,
            refresh_url=f"https://api.leiriaeats.com/drivers/{driver.gid}/stripe-onboarding-refresh",
            return_url=f"https://api.leiriaeats.com/drivers/{driver.gid}/stripe-onboarding-success",
            type="account_onboarding",
        )
        return {
            "onboarding_url": account_link.url,
            "stripe_account_id": driver.stripe_account_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{gid}/stripe-onboarding/complete")
def complete_driver_stripe_onboarding(gid: str, db: Session = Depends(get_db)):
    """
    POST /drivers/{gid}/stripe-onboarding/complete
    Verifica junto da Stripe se o onboarding foi concluído e sincroniza o estado local.
    """
    driver = _get_driver_by_gid_or_404(gid, db)
    if not driver.stripe_account_id:
        raise HTTPException(status_code=400, detail="Estafeta não tem conta Stripe.")

    try:
        account = stripe.Account.retrieve(driver.stripe_account_id)
        details_submitted = getattr(account, "details_submitted", False)
        charges_enabled = getattr(account, "charges_enabled", False)
        payouts_enabled = getattr(account, "payouts_enabled", False)
        is_complete = details_submitted and charges_enabled and payouts_enabled

        driver.stripe_onboarding_completed = is_complete
        if is_complete and driver.status != "ACTIVE":
            driver.status = "ACTIVE"
        db.commit()

        return {
            "onboarding_completed": is_complete,
            "charges_enabled": charges_enabled,
            "payouts_enabled": payouts_enabled,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{gid}/stripe-dashboard")
def get_driver_stripe_dashboard(gid: str, db: Session = Depends(get_db)):
    """
    POST /drivers/{gid}/stripe-dashboard
    Gera um link de uso único para o Stripe Express Dashboard do estafeta.
    """
    driver = _get_driver_by_gid_or_404(gid, db)
    if not driver.stripe_account_id:
        raise HTTPException(status_code=400, detail="Estafeta ainda não tem conta Stripe configurada.")

    try:
        login_link = stripe.Account.create_login_link(driver.stripe_account_id)
        return {
            "gid": driver.gid,
            "stripe_account_id": driver.stripe_account_id,
            "dashboard_url": login_link.url,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{gid}/profile", response_model=DriverProfileResponse)
def update_driver_profile(gid: str, payload: UpdateDriverProfileRequest, db: Session = Depends(get_db)):
    """
    PUT /drivers/{gid}/profile
    Guarda os dados pessoais/veículo do estafeta (PASSO 3 do registo).
    """
    driver = _get_driver_by_gid_or_404(gid, db)

    if payload.personal_info:
        for field in ("name", "phone", "email", "address", "city", "postal_code"):
            value = getattr(payload.personal_info, field)
            if value is not None:
                setattr(driver, field, value)

    if payload.vehicle_info:
        field_map = {"type": "vehicle_type", "plate": "vehicle_plate", "model": "vehicle_model", "color": "vehicle_color"}
        for src, dst in field_map.items():
            value = getattr(payload.vehicle_info, src)
            if value is not None:
                setattr(driver, dst, value)

    db.commit()
    db.refresh(driver)
    return driver


@router.get("/orders", response_model=List[dict])
def get_available_orders(driver_id: int, db: Session = Depends(get_db)):
    driver = _get_driver_or_404(driver_id, db)
    # Agora buscamos em SubOrderDB, identificando o estafeta pelo gid
    sub_orders = db.query(SubOrderDB).join(OrderDB).filter(
        SubOrderDB.driver_gid == driver.gid,
        SubOrderDB.status.in_(["Oferta enviada", "A aguardar estafeta", "A caminho"]),
    ).order_by(SubOrderDB.id.desc()).all()

    result = []
    for so in sub_orders:
        driver_to_rest = round(_haversine(driver.latitude, driver.longitude, so.restaurant_latitude, so.restaurant_longitude), 2) if driver.latitude and so.restaurant_latitude else None
        rest_to_dest = round(_haversine(so.restaurant_latitude, so.restaurant_longitude, so.master_order.delivery_latitude, so.master_order.delivery_longitude), 2) if so.restaurant_latitude and so.master_order.delivery_latitude else None
        
        result.append({
            "sub_order_id": so.id,
            "status": so.status,
            "restaurant_name": so.restaurant_name,
            "restaurant_latitude": so.restaurant_latitude,
            "restaurant_longitude": so.restaurant_longitude,
            "delivery_latitude": so.master_order.delivery_latitude,
            "delivery_longitude": so.master_order.delivery_longitude,
            "delivery_address": so.master_order.delivery_address,
            "customer_name": so.master_order.customer_name,
            "estimated_fee": _calculate_delivery_fee((driver_to_rest or 0) + (rest_to_dest or 0))
        })
    return result

@router.post("/{sub_order_id}/accept")
def accept_order(sub_order_id: int, driver_id: int, db: Session = Depends(get_db)):
    driver = _get_driver_or_404(driver_id, db)
    sub = db.query(SubOrderDB).filter(SubOrderDB.id == sub_order_id, SubOrderDB.driver_gid == driver.gid).first()
    if not sub: raise HTTPException(status_code=404, detail="Sub-pedido não encontrado ou não atribuído.")
    sub.status = "A aguardar estafeta"
    db.commit()
    return {"message": "Aceite."}

@router.post("/{sub_order_id}/reject")
def reject_order(sub_order_id: int, driver_id: int, db: Session = Depends(get_db)):
    """
    PLANO_RECOLHA_MULTI_RESTAURANTE.md, Fase 0.3 — endpoint que não existia: o app do
    estafeta (KomaPartner) chamava POST /drivers/{id}/orders/{id}/reject, que devolvia
    405 (rota inexistente), então o botão "Recusar" nunca funcionou de verdade contra a
    API real.

    Só remove a atribuição se o sub-pedido ainda estiver com ESTE estafeta (mesma guarda
    de accept_order) — evita recusar um sub-pedido que o worker já reatribuiu a outro.
    Devolve ao estado "Em Preparo" para o worker de despacho voltar a oferecer a outro
    estafeta, e limpa o estado em memória do worker (ver clear_dispatch_state) para não
    ficar preso fora do pool de notificação.
    """
    driver = _get_driver_or_404(driver_id, db)
    sub = db.query(SubOrderDB).filter(SubOrderDB.id == sub_order_id, SubOrderDB.driver_gid == driver.gid).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Sub-pedido não encontrado ou não atribuído.")

    sub.driver_gid = None
    sub.driver_name = None
    sub.driver_delivery_fee = None
    sub.driver_payment_transfer_id = None
    sub.status = "Em Preparo"
    db.commit()

    clear_dispatch_state(sub_order_id)

    return {"message": "Recusado.", "sub_order_id": sub_order_id, "status": sub.status}

@router.post("/{sub_order_id}/delivered")
def mark_as_delivered(sub_order_id: int, db: Session = Depends(get_db)):
    sub = db.query(SubOrderDB).filter(SubOrderDB.id == sub_order_id).first()
    if not sub: raise HTTPException(status_code=404, detail="Sub-pedido não encontrado.")
    sub.status = "Entregue"
    
    # Se todos os sub-pedidos do Master Order estiverem entregues, marcar Master como Entregue
    master = sub.master_order
    all_delivered = all(s.status == "Entregue" for s in master.sub_orders)
    if all_delivered:
        master.status = "Entregue"
    
    db.commit()
    return {"message": "Entregue."}


# ─── Rotas agrupadas (PLANO_RECOLHA_MULTI_RESTAURANTE.md, Fase 3) ──────────────────────
# A unidade que o estafeta aceita passa a ser a rota (N paragens de recolha + 1 entrega),
# não o sub-pedido isolado (accept/reject/delivered acima, mantidos por compatibilidade
# mas não usados mais pelo worker de despacho — ver courier_notification_service.py).

def _get_driver_route_or_404(route_gid: str, driver: DriverDB, db: Session) -> DeliveryRouteDB:
    route = db.query(DeliveryRouteDB).filter(
        DeliveryRouteDB.gid == route_gid,
        DeliveryRouteDB.driver_gid == driver.gid,
    ).first()
    if not route:
        raise HTTPException(status_code=404, detail="Rota não encontrada ou não atribuída.")
    return route


def _get_route_stop_or_404(route: DeliveryRouteDB, sub_order_id: int, db: Session) -> RouteStopDB:
    stop = next(
        (s for s in route.stops if s.sub_order is not None and s.sub_order.id == sub_order_id),
        None,
    )
    if not stop:
        raise HTTPException(status_code=404, detail="Paragem não encontrada nesta rota.")
    return stop


@router.get("/routes", response_model=List[dict])
def get_driver_routes(driver_id: int, db: Session = Depends(get_db)):
    """GET /drivers/routes?driver_id={id} — rotas ativas (OFFERED/ACCEPTED/IN_PROGRESS)
    atribuídas a este estafeta, com as paragens já sequenciadas pelo RouteSequencer."""
    driver = _get_driver_or_404(driver_id, db)
    routes = (
        db.query(DeliveryRouteDB)
        .filter(DeliveryRouteDB.driver_gid == driver.gid)
        .filter(DeliveryRouteDB.status.in_(["OFFERED", "ACCEPTED", "IN_PROGRESS"]))
        .order_by(DeliveryRouteDB.id.desc())
        .all()
    )
    result = []
    for route in routes:
        master = route.master_order
        stops = []
        for stop in sorted(route.stops, key=lambda s: s.sequence):
            sub = stop.sub_order
            stops.append({
                "sub_order_id": sub.id if sub else None,
                "sequence": stop.sequence,
                "restaurant_name": sub.restaurant_name if sub else None,
                "restaurant_latitude": sub.restaurant_latitude if sub else None,
                "restaurant_longitude": sub.restaurant_longitude if sub else None,
                "ready_at_estimated": stop.ready_at_estimated,
                "ready_at_confirmed": stop.ready_at_confirmed,
                "arrived_at": stop.arrived_at,
                "picked_up_at": stop.picked_up_at,
            })
        result.append({
            "route_gid": route.gid,
            "status": route.status,
            "estimated_delivery_at": route.estimated_delivery_at,
            "estimated_fee": route.estimated_fee,
            "delivery_latitude": master.delivery_latitude if master else None,
            "delivery_longitude": master.delivery_longitude if master else None,
            "delivery_address": master.delivery_address if master else None,
            "customer_name": master.customer_name if master else None,
            "stops": stops,
        })
    return result


@router.post("/routes/{route_gid}/accept")
def accept_route(route_gid: str, driver_id: int, db: Session = Depends(get_db)):
    driver = _get_driver_or_404(driver_id, db)
    route = _get_driver_route_or_404(route_gid, driver, db)
    if route.status != "OFFERED":
        raise HTTPException(status_code=409, detail=f"Rota não está mais disponível (status atual: {route.status}).")

    route.status = "ACCEPTED"
    route.accepted_at = datetime.now(timezone.utc)
    for stop in route.stops:
        if stop.sub_order:
            stop.sub_order.status = "A aguardar estafeta"
    db.commit()
    return {"message": "Rota aceite.", "route_gid": route_gid}


@router.post("/routes/{route_gid}/reject")
def reject_route(route_gid: str, driver_id: int, db: Session = Depends(get_db)):
    """
    Devolve os sub-pedidos da rota a "Em preparo" para o worker de despacho os
    reconsiderar no próximo ciclo — não há mais estado em memória a limpar aqui (Fase 3,
    item 4): a atribuição vivia só em DeliveryRouteDB.
    """
    driver = _get_driver_or_404(driver_id, db)
    route = _get_driver_route_or_404(route_gid, driver, db)

    route.status = "CANCELLED"
    route.driver_gid = None
    for stop in route.stops:
        if stop.sub_order:
            stop.sub_order.status = "Em preparo"
            stop.sub_order.driver_name = None
    db.commit()
    return {"message": "Rota recusada.", "route_gid": route_gid}


@router.post("/routes/{route_gid}/stops/{sub_order_id}/arrived")
def mark_stop_arrived(route_gid: str, sub_order_id: int, driver_id: int, db: Session = Depends(get_db)):
    driver = _get_driver_or_404(driver_id, db)
    route = _get_driver_route_or_404(route_gid, driver, db)
    stop = _get_route_stop_or_404(route, sub_order_id, db)

    stop.arrived_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "Chegada registada.", "sub_order_id": sub_order_id}


@router.post("/routes/{route_gid}/stops/{sub_order_id}/picked-up")
def mark_stop_picked_up(route_gid: str, sub_order_id: int, driver_id: int, db: Session = Depends(get_db)):
    driver = _get_driver_or_404(driver_id, db)
    route = _get_driver_route_or_404(route_gid, driver, db)
    stop = _get_route_stop_or_404(route, sub_order_id, db)

    stop.picked_up_at = datetime.now(timezone.utc)
    if stop.sub_order:
        stop.sub_order.status = "A caminho"
    if route.status == "ACCEPTED":
        route.status = "IN_PROGRESS"
    db.commit()
    return {"message": "Recolha registada.", "sub_order_id": sub_order_id}


@router.post("/routes/{route_gid}/complete")
def complete_route(route_gid: str, driver_id: int, db: Session = Depends(get_db)):
    driver = _get_driver_or_404(driver_id, db)
    route = _get_driver_route_or_404(route_gid, driver, db)

    now = datetime.now(timezone.utc)
    route.status = "COMPLETED"
    route.completed_at = now
    for stop in route.stops:
        if stop.sub_order:
            stop.sub_order.status = "Entregue"

    master = route.master_order
    if master and all(s.status == "Entregue" for s in master.sub_orders):
        master.status = "Entregue"

    db.commit()
    return {"message": "Entrega concluída.", "route_gid": route_gid}


@router.post("/location")
def update_location(driver_id: int, payload: DriverLocationUpdate, db: Session = Depends(get_db)):
    driver = _get_driver_or_404(driver_id, db)
    driver.latitude = payload.latitude
    driver.longitude = payload.longitude
    driver.last_seen = datetime.now(timezone.utc)
    db.commit()
    return {"status": "ok"}
