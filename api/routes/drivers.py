# Arquivo: api/routes/drivers.py
import hashlib
import math
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from starlette import status

from core import config
from core.database import get_db
from core.sql_models import DriverDB, OrderDB, RestaurantDB
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


# ──────────────────────────────────────────────────────────────
# Localização – rotas estáticas
# ATENÇÃO: devem ficar ANTES de GET /{driver_id} para o FastAPI
# não interpretar "online"/"nearest" como um driver_id inteiro.
# ──────────────────────────────────────────────────────────────

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância em km entre dois pontos geográficos (fórmula de Haversine)."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _calculate_delivery_fee(total_distance_km: float) -> float:
    """
    Calcula o valor total da entrega com base na distância percorrida pelo estafeta:
      - Tarifa base : €1,20
      - Por km      : €0,35
      - Mínimo      : €2,50

    A distância total é: estafeta → restaurante + restaurante → morada de entrega.
    """
    BASE_FARE   = 1.20
    RATE_PER_KM = 0.35
    MINIMUM_FEE = 2.50
    fee = BASE_FARE + total_distance_km * RATE_PER_KM
    return round(max(fee, MINIMUM_FEE), 2)


@router.get("/online")
def list_online_drivers(
    max_minutes: int = Query(default=2, ge=1, le=60,
                             description="Considera online estafetas que actualizaram a localização nos últimos N minutos"),
    db: Session = Depends(get_db),
):
    """
    Devolve todos os estafetas ACTIVE que enviaram a sua localização
    nos últimos `max_minutes` minutos.

    Útil para o painel admin ou para o algoritmo de atribuição de pedidos.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_minutes)

    online = db.query(DriverDB).filter(
        DriverDB.status == "ACTIVE",
        DriverDB.last_seen >= cutoff,
        DriverDB.latitude.isnot(None),
        DriverDB.longitude.isnot(None),
    ).all()

    return {
        "total_online": len(online),
        "max_minutes":  max_minutes,
        "drivers": [
            {
                "driver_id": d.id,
                "name":      d.name,
                "latitude":  d.latitude,
                "longitude": d.longitude,
                "last_seen": d.last_seen.isoformat() if d.last_seen else None,
            }
            for d in online
        ],
    }


@router.get("/nearest")
def find_nearest_driver(
    lat: float = Query(..., ge=-90,  le=90,  description="Latitude do ponto de referência (ex: restaurante)"),
    lng: float = Query(..., ge=-180, le=180, description="Longitude do ponto de referência"),
    max_minutes: int = Query(default=2, ge=1, le=60,
                             description="Considera apenas estafetas que actualizaram a localização nos últimos N minutos"),
    db: Session = Depends(get_db),
):
    """
    Devolve o estafeta ACTIVE mais próximo de um ponto (lat/lng),
    considerando apenas estafetas que actualizaram a localização
    nos últimos `max_minutes` minutos.

    Exemplo: GET /drivers/nearest?lat=39.74&lng=-8.80
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_minutes)

    candidates = db.query(DriverDB).filter(
        DriverDB.status == "ACTIVE",
        DriverDB.last_seen >= cutoff,
        DriverDB.latitude.isnot(None),
        DriverDB.longitude.isnot(None),
    ).all()

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=f"Nenhum estafeta online nos últimos {max_minutes} minuto(s).",
        )

    nearest = min(candidates, key=lambda d: _haversine(lat, lng, d.latitude, d.longitude))
    distance_km = _haversine(lat, lng, nearest.latitude, nearest.longitude)

    print(
        f"🗺️  Estafeta mais próximo de ({lat},{lng}): "
        f"id={nearest.id} ({nearest.name}) — {distance_km:.2f} km"
    )

    return {
        "driver_id":   nearest.id,
        "name":        nearest.name,
        "latitude":    nearest.latitude,
        "longitude":   nearest.longitude,
        "distance_km": round(distance_km, 2),
        "last_seen":   nearest.last_seen.isoformat() if nearest.last_seen else None,
    }


@router.get("/{driver_id}/orders/pending")
def get_pending_orders_for_driver(
    driver_id: int,
    db: Session = Depends(get_db),
):
    """
    O app do estafeta chama este endpoint via polling (ex: a cada 10s)
    para verificar se tem algum pedido atribuído a ele.

    Cada pedido inclui:
      - restaurant_latitude / restaurant_longitude → para mostrar no mapa
      - distance_km → distância actual do estafeta ao restaurante (km)

    Status devolvidos:
      - 'Oferta enviada'      → nova oferta aguarda aceitação/recusa do estafeta
      - 'A aguardar estafeta' → pedido aceite, estafeta deve ir buscar
      - 'A caminho'           → pedido recolhido, em entrega
    """
    driver = _get_driver_or_404(driver_id, db)

    orders = db.query(OrderDB).filter(
        OrderDB.driver_id == driver_id,
        OrderDB.status.in_(["Oferta enviada", "A aguardar estafeta", "A caminho"]),
    ).order_by(OrderDB.id.desc()).all()

    print(f"📡 [POLLING] Estafeta {driver_id} buscou pedidos pendentes. Encontrados: {len(orders)}")
    for o in orders:
        print(f"   ↳ Pedido #{o.id} | Status: {o.status} | Restaurante: {o.restaurant_name}")

    result = []
    for o in orders:
        # Dados do restaurante via relationship
        rest_lat     = o.restaurant.latitude  if o.restaurant else None
        rest_lng     = o.restaurant.longitude if o.restaurant else None
        rest_address = o.restaurant.address   if o.restaurant else None

        # Distância 1: posição actual do estafeta → restaurante
        driver_to_restaurant_km = None
        if (rest_lat is not None and rest_lng is not None
                and driver.latitude is not None and driver.longitude is not None):
            driver_to_restaurant_km = round(
                _haversine(driver.latitude, driver.longitude, rest_lat, rest_lng), 2
            )

        # Distância 2: restaurante → morada de entrega
        restaurant_to_delivery_km = None
        if (rest_lat is not None and rest_lng is not None
                and o.delivery_latitude is not None and o.delivery_longitude is not None):
            restaurant_to_delivery_km = round(
                _haversine(rest_lat, rest_lng, o.delivery_latitude, o.delivery_longitude), 2
            )

        # Valor total da entrega: tarifa base €1,20 + €0,35/km sobre a distância total
        # (estafeta→restaurante + restaurante→entrega), mínimo €2,50
        estimated_delivery_fee = None
        if driver_to_restaurant_km is not None and restaurant_to_delivery_km is not None:
            total_distance = driver_to_restaurant_km + restaurant_to_delivery_km
            estimated_delivery_fee = _calculate_delivery_fee(total_distance)

        result.append({
            "order_id":                   o.id,
            "status":                     o.status,
            "restaurant_name":            o.restaurant_name,
            "restaurant_address":         rest_address,
            "restaurant_latitude":        rest_lat,
            "restaurant_longitude":       rest_lng,
            "driver_to_restaurant_km":    driver_to_restaurant_km,
            "restaurant_to_delivery_km":  restaurant_to_delivery_km,
            "estimated_delivery_fee":     estimated_delivery_fee,
            "delivery_address":           o.delivery_address,
            "delivery_latitude":          o.delivery_latitude,
            "delivery_longitude":         o.delivery_longitude,
            "total":                      o.total,
            "tracking_code":              o.tracking_code,
            "created_at":                 o.created_at.isoformat() if o.created_at else None,
        })

    return {
        "driver_id":  driver_id,
        "driver_lat": driver.latitude,
        "driver_lng": driver.longitude,
        "total":      len(result),
        "orders":     result,
    }


# ──────────────────────────────────────────────────────────────
# Aceitar oferta de entrega
# ──────────────────────────────────────────────────────────────

@router.post("/{driver_id}/orders/{order_id}/accept")
def accept_order(driver_id: int, order_id: int, db: Session = Depends(get_db)):
    """
    O estafeta aceita a oferta de entrega.
    Muda o status do pedido de 'Oferta enviada' → 'A aguardar estafeta'.
    """
    _get_driver_or_404(driver_id, db)

    order = db.query(OrderDB).filter(
        OrderDB.id        == order_id,
        OrderDB.driver_id == driver_id,
        OrderDB.status    == "Oferta enviada",
    ).first()

    if not order:
        raise HTTPException(
            status_code=404,
            detail="Oferta não encontrada ou já expirou.",
        )

    order.status = "A aguardar estafeta"
    db.commit()

    # Remove do tracker de timeout (já foi aceite)
    from services.courier_notification_service import _pending_acceptance
    _pending_acceptance.pop(order_id, None)

    # Busca coordenadas do restaurante
    restaurant = db.query(RestaurantDB).filter(RestaurantDB.id == order.restaurant_id).first()
    restaurant_latitude  = restaurant.latitude  if restaurant else None
    restaurant_longitude = restaurant.longitude if restaurant else None

    print(
        f"✅ Pedido #{order_id} aceite pelo estafeta id={driver_id}. "
        f"Restaurante: lat={restaurant_latitude}, lng={restaurant_longitude}"
    )
    return {
        "message":              "Pedido aceite.",
        "order_id":             order_id,
        "status":               order.status,
        "restaurant_latitude":  restaurant_latitude,
        "restaurant_longitude": restaurant_longitude,
        "delivery_latitude":    order.delivery_latitude,
        "delivery_longitude":   order.delivery_longitude,
    }


# ──────────────────────────────────────────────────────────────
# Recusar oferta de entrega
# ──────────────────────────────────────────────────────────────

@router.post("/{driver_id}/orders/{order_id}/reject")
def reject_order(driver_id: int, order_id: int, db: Session = Depends(get_db)):
    """
    O estafeta recusa a oferta de entrega.
    Limpa a atribuição e devolve o pedido ao estado 'Em preparo'
    para que o worker tente atribuir ao próximo estafeta disponível.
    """
    _get_driver_or_404(driver_id, db)

    order = db.query(OrderDB).filter(
        OrderDB.id        == order_id,
        OrderDB.driver_id == driver_id,
        OrderDB.status    == "Oferta enviada",
    ).first()

    if not order:
        raise HTTPException(
            status_code=404,
            detail="Oferta não encontrada ou já expirou.",
        )

    print(f"❌ Pedido #{order_id} recusado pelo estafeta id={driver_id}. A tentar próximo.")

    # Limpa a atribuição → worker re-processa e tenta próximo driver
    order.driver_id   = None
    order.driver_name = None
    order.status      = "Em preparo"
    db.commit()

    # Remove do tracker de timeout e de notificados para permitir re-atribuição imediata
    from services.courier_notification_service import _pending_acceptance, _notified_order_ids
    _pending_acceptance.pop(order_id, None)
    _notified_order_ids.discard(order_id)

    return {"message": "Oferta recusada. A procurar próximo estafeta.", "order_id": order_id}


# ── /{driver_id} fica por ÚLTIMO entre os GETs ───────────────
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
# Stripe – Painel financeiro (Express Dashboard)
# ──────────────────────────────────────────────────────────────

@router.post("/{driver_id}/stripe-dashboard")
def get_driver_stripe_dashboard(driver_id: int, db: Session = Depends(get_db)):
    """
    Gera um link de acesso único ao painel financeiro do Stripe Express
    (Express Dashboard) para o estafeta.

    O link é de uso único e expira após alguns minutos — o app deve
    abri-lo directamente num WebView ou browser externo.

    Pré-requisito: o estafeta já deve ter concluído o onboarding Stripe
    (stripe_account_id preenchido e charges_enabled = true).
    """
    driver = _get_driver_or_404(driver_id, db)

    if not driver.stripe_account_id:
        raise HTTPException(
            status_code=400,
            detail="O estafeta não tem conta Stripe associada. Conclua o onboarding primeiro.",
        )

    try:
        login_link = stripe.Account.create_login_link(driver.stripe_account_id)
        print(f"💳 Dashboard link gerado para estafeta id={driver.id}: {login_link.url}")
        return {
            "driver_id":         driver.id,
            "stripe_account_id": driver.stripe_account_id,
            "dashboard_url":     login_link.url,
        }

    except stripe.error.InvalidRequestError as e:
        # Conta ainda não está activa / onboarding incompleto
        print(f"⚠️ Dashboard indisponível para estafeta id={driver.id}: {e}")
        raise HTTPException(
            status_code=400,
            detail="O painel financeiro ainda não está disponível. Certifique-se de que o onboarding Stripe foi concluído.",
        )
    except stripe.error.StripeError as e:
        print(f"❌ Erro Stripe (dashboard) estafeta id={driver.id}: {e}")
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
# Localização – polling do app do estafeta
# ──────────────────────────────────────────────────────────────

@router.patch("/{driver_id}/location")
def update_driver_location(
    driver_id: int,
    payload: DriverLocationUpdate,
    db: Session = Depends(get_db),
):
    """
    Recebe a posição GPS actual do estafeta, enviada pelo app via polling
    (recomendado: a cada 10–15 segundos enquanto o estafeta estiver ACTIVE).

    Guarda latitude, longitude e o timestamp do update (last_seen) na BD.
    """
    driver = _get_driver_or_404(driver_id, db)

    driver.latitude  = payload.latitude
    driver.longitude = payload.longitude
    driver.last_seen = datetime.now(timezone.utc)
    db.commit()

    print(
        f"📍 Localização actualizada — estafeta id={driver_id} "
        f"({driver.name}): lat={payload.latitude}, lng={payload.longitude}"
    )
    return {
        "driver_id": driver_id,
        "latitude":  driver.latitude,
        "longitude": driver.longitude,
        "last_seen": driver.last_seen.isoformat(),
    }


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
