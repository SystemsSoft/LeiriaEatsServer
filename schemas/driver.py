# Arquivo: schemas/driver.py
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────
# Sub-DTOs  (espelham os data classes Kotlin)
# ──────────────────────────────────────────────────────────────

class DriverPersonalInfoDto(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None

    model_config = {"populate_by_name": True}


class DriverVehicleInfoDto(BaseModel):
    type: Optional[str] = None          # MOTORCYCLE, BICYCLE, CAR, etc.
    plate: Optional[str] = None
    model: Optional[str] = None
    color: Optional[str] = None

    model_config = {"populate_by_name": True}


# ──────────────────────────────────────────────────────────────
# Payload principal enviado pelo app do estafeta
# ──────────────────────────────────────────────────────────────

class UpdateDriverProfileRequest(BaseModel):
    personal_info: Optional[DriverPersonalInfoDto] = Field(None, alias="personal_info")
    vehicle_info:  Optional[DriverVehicleInfoDto]  = Field(None, alias="vehicle_info")

    model_config = {"populate_by_name": True}


# ──────────────────────────────────────────────────────────────
# Localização — payload enviado pelo app via polling
# ──────────────────────────────────────────────────────────────

class DriverLocationUpdate(BaseModel):
    latitude:  float = Field(..., ge=-90,  le=90,  description="Latitude GPS do estafeta")
    longitude: float = Field(..., ge=-180, le=180, description="Longitude GPS do estafeta")

    model_config = {"populate_by_name": True}


# ──────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────

class DriverRegisterRequest(BaseModel):
    login: str
    password: str

    personal_info: Optional[DriverPersonalInfoDto] = Field(None, alias="personal_info")
    vehicle_info:  Optional[DriverVehicleInfoDto]  = Field(None, alias="vehicle_info")

    model_config = {"populate_by_name": True}


class DriverLoginRequest(BaseModel):
    login: str
    password: str


class DriverLoginResponse(BaseModel):
    authenticated: bool
    driver_id: int
    name: str
    status: str
    message: str


# ──────────────────────────────────────────────────────────────
# Resposta de perfil completo
# ──────────────────────────────────────────────────────────────

class DriverProfileResponse(BaseModel):
    id: int
    login: str
    status: str

    # personal
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None

    # vehicle
    vehicle_type: Optional[str] = None
    vehicle_plate: Optional[str] = None
    vehicle_model: Optional[str] = None
    vehicle_color: Optional[str] = None

    # localização
    latitude:  Optional[float]    = None
    longitude: Optional[float]    = None
    last_seen: Optional[datetime] = None

    model_config = {"from_attributes": True}
