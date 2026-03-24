# Arquivo: schemas/driver.py
from typing import Optional
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────
# Sub-DTOs  (espelham os data classes Kotlin)
# ──────────────────────────────────────────────────────────────

class DriverPersonalInfoDto(BaseModel):
    name: str
    phone: str
    email: str
    birth_date: str = Field(..., alias="birth_date")
    address: str
    city: str
    postal_code: str = Field(..., alias="postal_code")
    cc: str

    model_config = {"populate_by_name": True}


class DriverFiscalInfoDto(BaseModel):
    nif: str
    niss: Optional[str] = None
    iban: str

    model_config = {"populate_by_name": True}


class DriverVehicleInfoDto(BaseModel):
    type: str                          # MOTORCYCLE, BICYCLE, CAR, etc.
    plate: str
    model: str
    color: str
    carta_conducao: str = Field(..., alias="carta_conducao")
    carta_conducao_categoria: str = Field(..., alias="carta_conducao_categoria")

    model_config = {"populate_by_name": True}


# ──────────────────────────────────────────────────────────────
# Payload principal enviado pelo app do estafeta
# ──────────────────────────────────────────────────────────────

class UpdateDriverProfileRequest(BaseModel):
    personal_info: DriverPersonalInfoDto = Field(..., alias="personal_info")
    fiscal_info: DriverFiscalInfoDto     = Field(..., alias="fiscal_info")
    vehicle_info: DriverVehicleInfoDto   = Field(..., alias="vehicle_info")

    model_config = {"populate_by_name": True}


# ──────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────

class DriverRegisterRequest(BaseModel):
    login: str
    password: str

    # Perfil completo — opcional no registo.
    # O app pode enviar tudo de uma vez OU só as credenciais
    # e preencher o perfil depois via PUT /{id}/profile.
    personal_info: Optional[DriverPersonalInfoDto] = Field(None, alias="personal_info")
    fiscal_info:   Optional[DriverFiscalInfoDto]   = Field(None, alias="fiscal_info")
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
    profile_complete: bool
    message: str


# ──────────────────────────────────────────────────────────────
# Resposta de perfil completo
# ──────────────────────────────────────────────────────────────

class DriverProfileResponse(BaseModel):
    id: int
    login: str
    status: str
    profile_complete: bool = False

    # personal
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    birth_date: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    cc: Optional[str] = None

    # fiscal
    nif: Optional[str] = None
    niss: Optional[str] = None
    iban: Optional[str] = None

    # vehicle
    vehicle_type: Optional[str] = None
    vehicle_plate: Optional[str] = None
    vehicle_model: Optional[str] = None
    vehicle_color: Optional[str] = None
    carta_conducao: Optional[str] = None
    carta_conducao_categoria: Optional[str] = None

    # stripe
    stripe_account_id: Optional[str] = None
    stripe_onboarding_completed: bool = False

    model_config = {"from_attributes": True}

