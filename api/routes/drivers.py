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
from core.sql_models import DriverDB, OrderDB, RestaurantDB, SubOrderDB
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

@router.post("/location")
def update_location(driver_id: int, payload: DriverLocationUpdate, db: Session = Depends(get_db)):
    driver = _get_driver_or_404(driver_id, db)
    driver.latitude = payload.latitude
    driver.longitude = payload.longitude
    driver.last_seen = datetime.now(timezone.utc)
    db.commit()
    return {"status": "ok"}
