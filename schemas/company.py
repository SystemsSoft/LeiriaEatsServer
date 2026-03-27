# Arquivo: schemas/company.py
from pydantic import BaseModel
from typing import Optional

from schemas.models import Product
from schemas.product import ProductCreateRequest


# ==========================================
# 🕐 SCHEMAS DE HORÁRIOS
# ==========================================

class RestaurantHourRequest(BaseModel):
    id: Optional[int] = None
    restaurant_id: int
    day_of_week: int      # 0=Domingo ... 6=Sábado
    open_time: str        # "HH:mm"
    close_time: str       # "HH:mm"
    is_closed: bool = False


class RestaurantHourResponse(BaseModel):
    id: int
    restaurant_id: int
    day_of_week: int
    open_time: str
    close_time: str
    is_closed: bool

    class Config:
        from_attributes = True


# REQUEST: O que o app envia para criar a empresa
class CompanyCreateRequest(BaseModel):
    name: str
    category: str
    phone: str
    address: str
    image_url: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    login: str
    password: str
    license: str

    plan: Optional[str] = None

class CompanyUpdateRequest(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    image_url: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class UsesPlatformCourierRequest(BaseModel):
    use_own_delivery: bool


# ==========================================
# 📦 SCHEMAS DE ZONAS DE ENTREGA
# ==========================================

class DeliveryZoneRequest(BaseModel):
    zone: int
    radius_km: float
    price: float
    enabled: bool = True
    center_lat: Optional[float] = None
    center_lng: Optional[float] = None


class DeliveryZoneResponse(BaseModel):
    id: int
    restaurant_id: int
    zone: int
    radius_km: float
    price: float
    enabled: bool
    center_lat: Optional[float] = None
    center_lng: Optional[float] = None

    class Config:
        from_attributes = True


# RESPONSE: O que o app recebe de volta
class CompanyResponse(BaseModel):
    id: int
    name: str
    category: str
    phone: str
    address: str
    image_url: Optional[str]
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    products: list[Product]

    # --- NOVOS CAMPOS ---
    login: str
    license: str
    plan: Optional[str] = None
    use_own_delivery: bool = False

    # OBS: NUNCA retornamos o campo 'password' aqui por segurança

    class Config:
        from_attributes = True