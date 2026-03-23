# Arquivo: api/routes/drivers.py
import hashlib
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette import status

from core.database import get_db
from core.sql_models import DriverDB
from schemas.driver import (
    DriverRegisterRequest,
    DriverLoginRequest,
    DriverLoginResponse,
    UpdateDriverProfileRequest,
    DriverProfileResponse,
)

router = APIRouter(prefix="/drivers", tags=["Estafetas"])


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _hash_password(plain: str) -> str:
    """SHA-256 simples – substitua por bcrypt em produção."""
    return hashlib.sha256(plain.encode()).hexdigest()


def _verify_password(plain: str, hashed: str) -> bool:
    return _hash_password(plain) == hashed


def _get_driver_or_404(driver_id: int, db: Session) -> DriverDB:
    driver = db.query(DriverDB).filter(DriverDB.id == driver_id).first()
    if not driver:
        raise HTTPException(status_code=404, detail="Estafeta não encontrado.")
    return driver


# ──────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_driver(payload: DriverRegisterRequest, db: Session = Depends(get_db)):
    """Regista um novo estafeta com login e password."""
    existing = db.query(DriverDB).filter(DriverDB.login == payload.login).first()
    if existing:
        raise HTTPException(status_code=400, detail="Login já existe.")

    driver = DriverDB(
        login=payload.login,
        password=_hash_password(payload.password),
        status="PENDING",
    )
    db.add(driver)
    db.commit()
    db.refresh(driver)

    print(f"✅ Novo estafeta registado: {driver.login} (id={driver.id})")
    return {"message": "Estafeta registado com sucesso!", "driver_id": driver.id}


@router.post("/login", response_model=DriverLoginResponse)
def login_driver(payload: DriverLoginRequest, db: Session = Depends(get_db)):
    """Autenticação do estafeta."""
    driver = db.query(DriverDB).filter(DriverDB.login == payload.login).first()

    if not driver or not _verify_password(payload.password, driver.password):
        raise HTTPException(status_code=401, detail="Login ou senha incorretos.")

    print(f"🔐 Estafeta autenticado: {driver.login}")
    return DriverLoginResponse(
        authenticated=True,
        driver_id=driver.id,
        name=driver.name or "",
        status=driver.status,
        message="Login realizado com sucesso.",
    )


# ──────────────────────────────────────────────────────────────
# Perfil – leitura
# ──────────────────────────────────────────────────────────────

@router.get("/{driver_id}", response_model=DriverProfileResponse)
def get_driver_profile(driver_id: int, db: Session = Depends(get_db)):
    """Retorna o perfil completo de um estafeta."""
    return _get_driver_or_404(driver_id, db)


@router.get("/", response_model=List[DriverProfileResponse])
def list_drivers(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """Lista todos os estafetas (uso administrativo)."""
    return db.query(DriverDB).offset(skip).limit(limit).all()


# ──────────────────────────────────────────────────────────────
# Perfil – actualização (payload do app)
# ──────────────────────────────────────────────────────────────

@router.put("/{driver_id}/profile", response_model=DriverProfileResponse)
def update_driver_profile(
    driver_id: int,
    payload: UpdateDriverProfileRequest,
    db: Session = Depends(get_db),
):
    """
    Recebe o UpdateDriverProfileRequest enviado pelo app do estafeta e
    actualiza (ou preenche) todas as secções: personal_info, fiscal_info e
    vehicle_info.
    """
    driver = _get_driver_or_404(driver_id, db)

    p = payload.personal_info
    driver.name        = p.name
    driver.phone       = p.phone
    driver.email       = p.email
    driver.birth_date  = p.birth_date
    driver.address     = p.address
    driver.city        = p.city
    driver.postal_code = p.postal_code
    driver.cc          = p.cc

    f = payload.fiscal_info
    driver.nif  = f.nif
    driver.niss = f.niss
    driver.iban = f.iban

    v = payload.vehicle_info
    driver.vehicle_type             = v.type
    driver.vehicle_plate            = v.plate
    driver.vehicle_model            = v.model
    driver.vehicle_color            = v.color
    driver.carta_conducao           = v.carta_conducao
    driver.carta_conducao_categoria = v.carta_conducao_categoria

    # Após o preenchimento completo o estafeta fica em revisão
    if driver.status == "PENDING":
        driver.status = "REVIEW"

    db.commit()
    db.refresh(driver)

    print(f"📝 Perfil actualizado: estafeta id={driver.id} ({driver.name})")
    return driver


# ──────────────────────────────────────────────────────────────
# Estado – activação / desactivação
# ──────────────────────────────────────────────────────────────

@router.patch("/{driver_id}/status", response_model=DriverProfileResponse)
def update_driver_status(
    driver_id: int,
    new_status: str,
    db: Session = Depends(get_db),
):
    """
    Altera o estado do estafeta.
    Valores aceites: PENDING | REVIEW | ACTIVE | INACTIVE
    """
    allowed = {"PENDING", "REVIEW", "ACTIVE", "INACTIVE"}
    if new_status.upper() not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Estado inválido. Valores aceites: {allowed}",
        )

    driver = _get_driver_or_404(driver_id, db)
    driver.status = new_status.upper()
    db.commit()
    db.refresh(driver)

    print(f"🔄 Estado do estafeta id={driver.id} alterado para {driver.status}")
    return driver


# ──────────────────────────────────────────────────────────────
# Remoção
# ──────────────────────────────────────────────────────────────

@router.delete("/{driver_id}", status_code=status.HTTP_200_OK)
def delete_driver(driver_id: int, db: Session = Depends(get_db)):
    """Remove permanentemente um estafeta."""
    driver = _get_driver_or_404(driver_id, db)
    db.delete(driver)
    db.commit()
    print(f"🗑️ Estafeta id={driver_id} removido.")
    return {"message": f"Estafeta id={driver_id} removido com sucesso."}

