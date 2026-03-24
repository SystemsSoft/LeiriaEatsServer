# Arquivo: api/routes/drivers.py
import hashlib
from typing import List

import stripe
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette import status

from core import config
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
stripe.api_key = config.settings.STRIPE_API_KEY


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


def _stripe_email(login: str) -> str:
    """Garante que o login seja um e-mail válido para o Stripe."""
    return login if "@" in login else f"{login}@Komaai.com"


def _apply_profile(driver: DriverDB, payload: DriverRegisterRequest) -> None:
    """Aplica os dados de perfil ao objecto DriverDB quando presentes no payload."""
    if payload.personal_info:
        p = payload.personal_info
        if p.name        is not None: driver.name        = p.name
        if p.phone       is not None: driver.phone       = p.phone
        if p.email       is not None: driver.email       = p.email
        if p.address     is not None: driver.address     = p.address
        if p.city        is not None: driver.city        = p.city
        if p.postal_code is not None: driver.postal_code = p.postal_code

    if payload.vehicle_info:
        v = payload.vehicle_info
        if v.type  is not None: driver.vehicle_type  = v.type
        if v.plate is not None: driver.vehicle_plate = v.plate
        if v.model is not None: driver.vehicle_model = v.model
        if v.color is not None: driver.vehicle_color = v.color


# ──────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_driver(payload: DriverRegisterRequest, db: Session = Depends(get_db)):
    """
    Regista um novo estafeta seguindo o mesmo fluxo dos restaurantes:
      1. Guarda as credenciais na BD.
      2. Aplica dados de perfil se enviados de uma vez (todos opcionais).
      3. Cria uma conta Stripe Express.
      4. Gera o link de onboarding do Stripe (UI hospedada pelo Stripe).
      5. Devolve o link → app abre em WebView → utilizador preenche dados no Stripe
         → Stripe redireciona para return_url.
    """
    existing = db.query(DriverDB).filter(DriverDB.login == payload.login).first()
    if existing:
        raise HTTPException(status_code=400, detail="Login já existe.")

    # 1. Persiste o estafeta
    driver = DriverDB(
        login=payload.login,
        password=_hash_password(payload.password),
        status="PENDING",
    )
    db.add(driver)
    db.commit()
    db.refresh(driver)

    # 2. Aplica perfil se enviado junto com o registo
    _apply_profile(driver, payload)
    db.commit()

    try:
        # 3. Cria conta Stripe Express (sem pré-preenchimento — dados serão lidos do Stripe após onboarding)
        print(f"✨ Criando conta Stripe Express para estafeta id={driver.id} ...")
        p = payload.personal_info
        account = stripe.Account.create(
            type="express",
            country="PT",
            email=p.email if (p and p.email) else _stripe_email(payload.login),
            capabilities={
                "card_payments": {"requested": True},
                "transfers":     {"requested": True},
            },
            metadata={"driver_id": str(driver.id)},
        )
        driver.stripe_account_id = account.id
        driver.status = "STRIPE_PENDING"   # conta Stripe criada, aguarda onboarding
        db.commit()
        print(f"✅ Conta Stripe criada: {account.id} → estafeta id={driver.id}")

        # 4. Gera o link de onboarding (UI do Stripe — igual ao restaurante)
        account_link = stripe.AccountLink.create(
            account=driver.stripe_account_id,
            refresh_url="http://localhost:8080/#/driver/onboarding-retry",
            return_url="http://localhost:8080/#/driver/onboarding-success",
            type="account_onboarding",
        )
        print(f"🔗 Onboarding link gerado para estafeta id={driver.id}")

        return {
            "message":           "Estafeta registado com sucesso!",
            "driver_id":         driver.id,
            "status":            driver.status,
            "stripe_account_id": driver.stripe_account_id,
            "onboarding_url":    account_link.url,   # ← app abre este URL no WebView
        }

    except stripe.error.StripeError as e:
        print(f"⚠️ Stripe indisponível para estafeta id={driver.id}: {e}")
        return {
            "message":           "Estafeta registado, mas a conta Stripe não pôde ser criada agora.",
            "driver_id":         driver.id,
            "status":            driver.status,
            "stripe_account_id": None,
            "onboarding_url":    None,
        }


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

@router.get("/", response_model=List[DriverProfileResponse])
def list_drivers(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """Lista todos os estafetas (uso administrativo)."""
    return db.query(DriverDB).offset(skip).limit(limit).all()


@router.get("/{driver_id}", response_model=DriverProfileResponse)
def get_driver_profile(driver_id: int, db: Session = Depends(get_db)):
    """Retorna o perfil completo de um estafeta."""
    return _get_driver_or_404(driver_id, db)


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
    actualiza todas as secções: personal_info, fiscal_info e vehicle_info.
    Apenas os campos presentes (não None) são gravados.
    """
    driver = _get_driver_or_404(driver_id, db)

    if payload.personal_info:
        p = payload.personal_info
        if p.name        is not None: driver.name        = p.name
        if p.phone       is not None: driver.phone       = p.phone
        if p.email       is not None: driver.email       = p.email
        if p.address     is not None: driver.address     = p.address
        if p.city        is not None: driver.city        = p.city
        if p.postal_code is not None: driver.postal_code = p.postal_code

    if payload.vehicle_info:
        v = payload.vehicle_info
        if v.type  is not None: driver.vehicle_type  = v.type
        if v.plate is not None: driver.vehicle_plate = v.plate
        if v.model is not None: driver.vehicle_model = v.model
        if v.color is not None: driver.vehicle_color = v.color

    db.commit()
    db.refresh(driver)

    print(f"📝 Perfil actualizado: estafeta id={driver.id} ({driver.name})")
    return driver


# ──────────────────────────────────────────────────────────────
# Stripe Connect – Onboarding
# ──────────────────────────────────────────────────────────────

@router.post("/{driver_id}/stripe-onboarding")
def create_driver_stripe_onboarding(driver_id: int, db: Session = Depends(get_db)):
    """
    Gera (ou re-gera) o link de onboarding do Stripe Connect para o estafeta.
    Se ainda não tiver conta Stripe, cria uma Express antes de gerar o link.
    """
    driver = _get_driver_or_404(driver_id, db)

    try:
        # Cria a conta Stripe Express se ainda não existir
        if not driver.stripe_account_id:
            print(f"✨ Criando conta Stripe Express para estafeta id={driver.id} ...")
            account = stripe.Account.create(
                type="express",
                country="PT",
                email=driver.email or _stripe_email(driver.login),
                capabilities={
                    "card_payments": {"requested": True},
                    "transfers":     {"requested": True},
                },
                metadata={"driver_id": str(driver.id)},
            )
            driver.stripe_account_id = account.id
            db.commit()
            print(f"✅ Conta Stripe criada: {account.id}")

        # Gera o link de onboarding
        account_link = stripe.AccountLink.create(
            account=driver.stripe_account_id,
            refresh_url="http://localhost:8080/#/driver/onboarding-retry",
            return_url="http://localhost:8080/#/driver/onboarding-success",
            type="account_onboarding",
        )

        print(f"🔗 Onboarding link gerado para estafeta id={driver.id}")
        return {
            "onboarding_url":    account_link.url,
            "stripe_account_id": driver.stripe_account_id,
        }

    except stripe.error.StripeError as e:
        print(f"❌ Erro Stripe no onboarding do estafeta id={driver.id}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{driver_id}/stripe-onboarding/complete")
def mark_driver_onboarding_complete(driver_id: int, db: Session = Depends(get_db)):
    """
    Marca o onboarding Stripe do estafeta como concluído, verifica o estado
    real da conta na API do Stripe e sincroniza os dados preenchidos lá
    (nome, telefone, email, morada) de volta para a BD.
    """
    driver = _get_driver_or_404(driver_id, db)

    if not driver.stripe_account_id:
        raise HTTPException(status_code=400, detail="O estafeta não tem conta Stripe associada.")

    try:
        # expand=["individual"] necessário para obter os dados pessoais preenchidos no onboarding
        account = stripe.Account.retrieve(
            driver.stripe_account_id,
            expand=["individual"],
        )
        is_complete = (
            account.get("details_submitted", False) and
            account.get("charges_enabled", False) and
            account.get("payouts_enabled", False)
        )
        driver.stripe_onboarding_completed = is_complete

        # ── Sincroniza dados Stripe → BD ────────────────────────────────────
        if is_complete:
            individual = account.get("individual") or {}

            first_name = individual.get("first_name") or ""
            last_name  = individual.get("last_name")  or ""
            full_name  = f"{first_name} {last_name}".strip()
            if full_name:
                driver.name = full_name

            phone = individual.get("phone") or account.get("phone")
            if phone:
                driver.phone = phone

            email = individual.get("email") or account.get("email")
            if email:
                driver.email = email

            addr = individual.get("address") or {}
            if addr.get("line1"):
                driver.address = addr["line1"]
            if addr.get("city"):
                driver.city = addr["city"]
            if addr.get("postal_code"):
                driver.postal_code = addr["postal_code"]

            driver.status = "ACTIVE"   # onboarding concluído → estafeta activo

            print(
                f"✅ Dados Stripe sincronizados → estafeta id={driver.id}: "
                f"name={driver.name}, phone={driver.phone}, "
                f"email={driver.email}, address={driver.address}"
            )

        db.commit()
        db.refresh(driver)

        return {
            "driver_id":            driver.id,
            "stripe_account_id":    driver.stripe_account_id,
            "onboarding_completed": is_complete,
            "charges_enabled":      account.get("charges_enabled"),
            "payouts_enabled":      account.get("payouts_enabled"),
            # dados sincronizados do Stripe:
            "synced_name":          driver.name,
            "synced_phone":         driver.phone,
            "synced_email":         driver.email,
            "synced_address":       driver.address,
            "synced_city":          driver.city,
            "synced_postal_code":   driver.postal_code,
        }

    except stripe.error.StripeError as e:
        print(f"❌ Erro Stripe no complete do estafeta id={driver.id}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ──────────────────────────────────────────────────────────────
# Stripe – Saldo do estafeta
# ──────────────────────────────────────────────────────────────

@router.get("/{driver_id}/stripe-balance")
def get_driver_stripe_balance(driver_id: int, db: Session = Depends(get_db)):
    """Retorna o saldo Stripe do estafeta (disponível e pendente)."""
    driver = _get_driver_or_404(driver_id, db)

    if not driver.stripe_account_id:
        raise HTTPException(status_code=400, detail="O estafeta não tem conta Stripe associada.")

    try:
        balance = stripe.Balance.retrieve(stripe_account=driver.stripe_account_id)

        available = sum(b.amount for b in balance.available) / 100.0
        pending   = sum(b.amount for b in balance.pending)   / 100.0

        payouts = stripe.Payout.list(
            limit=5,
            stripe_account=driver.stripe_account_id,
        )
        recent_payouts = [
            {
                "amount":   p.amount / 100.0,
                "status":   p.status,
                "arrival":  p.arrival_date,
            }
            for p in payouts.data
        ]

        return {
            "driver_id":         driver.id,
            "stripe_account_id": driver.stripe_account_id,
            "saldo_disponivel_eur": available,
            "saldo_pendente_eur":   pending,
            "ultimos_repasses":     recent_payouts,
        }

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))


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
    Valores aceites: PENDING | STRIPE_PENDING | REVIEW | ACTIVE | INACTIVE
    """
    allowed = {"PENDING", "STRIPE_PENDING", "REVIEW", "ACTIVE", "INACTIVE"}
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
