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
    driver = DriverDB(login=payload.login, password=_hash_password(payload.password), status="PENDING")
    db.add(driver)
    db.commit()
    db.refresh(driver)
    return {"message": "Registado. Complete o onboarding Stripe."}

@router.get("/orders", response_model=List[dict])
def get_available_orders(driver_id: int, db: Session = Depends(get_db)):
    driver = _get_driver_or_404(driver_id, db)
    # Agora buscamos em SubOrderDB
    sub_orders = db.query(SubOrderDB).join(OrderDB).filter(
        SubOrderDB.driver_id == driver_id,
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
    sub = db.query(SubOrderDB).filter(SubOrderDB.id == sub_order_id, SubOrderDB.driver_id == driver_id).first()
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
