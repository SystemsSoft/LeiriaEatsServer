# Arquivo: api/routes/drivers.py
import hashlib
import hmac
import math
import secrets
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette import status
from ulid import ULID

from core import config
from core.database import get_db
from core.sql_models import DriverDB, OrderDB, RestaurantDB, SubOrderDB, DeliveryRouteDB, RouteStopDB
from services.courier_notification_service import (
    clear_dispatch_state,
    close_cancelled_routes,
    is_sub_order_cancelled,
)
from services.driver_payout_service import pay_driver_for_route
from schemas.driver import (
    DriverRegisterRequest,
    DriverLoginRequest,
    DriverLoginResponse,
    DriverLocationUpdate,
    DriverFcmTokenUpdate,
    UpdateDriverProfileRequest,
    DriverProfileResponse,
    RouteCompleteRequest,
)

router = APIRouter(prefix="/drivers", tags=["Estafetas"])
stripe.api_key = config.settings.STRIPE_API_KEY

# PBKDF2 com salt por estafeta (só stdlib). Formato: pbkdf2_sha256$<iterações>$<salt>$<hash>.
# Contas antigas guardavam sha256(plain) sem salt — continuam a entrar e são convertidas
# no primeiro login (_needs_rehash).
_PBKDF2_ITERATIONS = 310_000


def _hash_password(plain: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), _PBKDF2_ITERATIONS).hex()
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest}"


def _verify_password(plain: str, hashed: str) -> bool:
    if hashed and hashed.startswith("pbkdf2_sha256$"):
        _, iterations, salt, digest = hashed.split("$", 3)
        candidate = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), int(iterations)).hex()
        return hmac.compare_digest(candidate, digest)
    return hmac.compare_digest(hashlib.sha256(plain.encode()).hexdigest(), hashed or "")


def _needs_rehash(hashed: str) -> bool:
    return not (hashed or "").startswith(f"pbkdf2_sha256${_PBKDF2_ITERATIONS}$")

def _get_driver_or_404(driver_id: str, db: Session) -> DriverDB:
    """`driver_id` é o gid devolvido no login (o app KomaPartner só conhece o gid);
    o id numérico continua aceite por compatibilidade."""
    return _get_driver_by_gid_or_404(str(driver_id), db)

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
    login = payload.login.strip()
    if not login or len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Login obrigatório e palavra-passe com pelo menos 6 caracteres.")
    existing = db.query(DriverDB).filter(DriverDB.login == login).first()
    if existing: raise HTTPException(status_code=400, detail="Login já existe.")
    driver = DriverDB(gid=str(ULID()), login=login, password=_hash_password(payload.password), status="PENDING")
    # O app do estafeta envia os dados pessoais/veículo já no registo (antes eram ignorados).
    _apply_profile(driver, payload.personal_info, payload.vehicle_info)
    db.add(driver)
    db.commit()
    db.refresh(driver)
    return {
        "gid": driver.gid,
        "name": driver.name,
        "status": driver.status,
        "profile_complete": bool(driver.name and driver.vehicle_type),
        "message": "Registado. Complete o onboarding Stripe.",
    }


@router.post("/login", response_model=DriverLoginResponse)
def login_driver(payload: DriverLoginRequest, db: Session = Depends(get_db)):
    driver = db.query(DriverDB).filter(DriverDB.login == payload.login).first()
    if not driver or not _verify_password(payload.password, driver.password):
        raise HTTPException(status_code=401, detail="Login ou senha inválidos.")
    if _needs_rehash(driver.password):
        driver.password = _hash_password(payload.password)
        db.commit()

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

    stripe_email = driver.email or (driver.login if "@" in driver.login else f"{driver.login}@leiriaeats.com")
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


def _apply_profile(driver: DriverDB, personal_info, vehicle_info) -> None:
    if personal_info:
        for field in ("name", "phone", "email", "address", "city", "postal_code"):
            value = getattr(personal_info, field)
            if value is not None:
                setattr(driver, field, value)

    if vehicle_info:
        field_map = {"type": "vehicle_type", "plate": "vehicle_plate", "model": "vehicle_model", "color": "vehicle_color"}
        for src, dst in field_map.items():
            value = getattr(vehicle_info, src)
            if value is not None:
                setattr(driver, dst, value)


def _onboarding_link(driver: DriverDB) -> str:
    account_link = stripe.AccountLink.create(
        account=driver.stripe_account_id,
        refresh_url=f"https://api.leiriaeats.com/drivers/{driver.gid}/stripe-onboarding-refresh",
        return_url=f"https://api.leiriaeats.com/drivers/{driver.gid}/stripe-onboarding-success",
        type="account_onboarding",
    )
    return account_link.url


def _sync_stripe_onboarding(driver: DriverDB, db: Session) -> dict:
    """Lê a conta na Stripe e ativa o estafeta quando o onboarding estiver completo."""
    account = stripe.Account.retrieve(driver.stripe_account_id)
    details_submitted = getattr(account, "details_submitted", False)
    charges_enabled = getattr(account, "charges_enabled", False)
    payouts_enabled = getattr(account, "payouts_enabled", False)
    is_complete = bool(details_submitted and charges_enabled and payouts_enabled)

    driver.stripe_onboarding_completed = is_complete
    if is_complete and driver.status != "ACTIVE":
        driver.status = "ACTIVE"
    db.commit()
    return {
        "onboarding_completed": is_complete,
        "charges_enabled": charges_enabled,
        "payouts_enabled": payouts_enabled,
        "status": driver.status,
    }


def _partner_page(title: str, message: str) -> HTMLResponse:
    """Página simples (identidade Koma) mostrada no browser ao voltar da Stripe."""
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="pt"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} - Koma Partner</title>
<style>
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center; padding:20px;
         font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         background:linear-gradient(180deg,#0F3D2E 0%,#207659 100%); }}
  .card {{ background:#fff; border-radius:20px; padding:40px 28px; max-width:420px; width:100%; text-align:center; }}
  h1 {{ color:#111827; font-size:22px; margin:0 0 12px; }}
  p {{ color:#6B7280; font-size:16px; line-height:1.5; margin:0; }}
  .hint {{ color:#1A7A5E; font-weight:700; margin-top:24px; }}
</style></head>
<body><div class="card"><h1>{title}</h1><p>{message}</p><p class="hint">Já pode fechar esta página e voltar ao app Koma Partner.</p></div></body></html>""")


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

        return {
            "onboarding_url": _onboarding_link(driver),
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
        return _sync_stripe_onboarding(driver, db)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{gid}/stripe-onboarding-success", response_class=HTMLResponse)
def driver_stripe_onboarding_success(gid: str, db: Session = Depends(get_db)):
    """return_url do AccountLink: o estafeta cai aqui no browser ao terminar na Stripe.
    Sincroniza já o estado, para o app ver a conta ativa no próximo poll."""
    driver = _get_driver_by_gid_or_404(gid, db)
    try:
        result = _sync_stripe_onboarding(driver, db) if driver.stripe_account_id else {"onboarding_completed": False}
    except Exception:
        result = {"onboarding_completed": False}
    if result["onboarding_completed"]:
        return _partner_page("Pagamentos configurados", "A sua conta está ativa. Já pode ficar disponível e receber entregas.")
    return _partner_page(
        "Quase lá",
        "A Stripe ainda está a validar os seus dados. No app, toque em \"Já concluí\" daqui a pouco "
        "ou volte a abrir a configuração de pagamentos.",
    )


@router.get("/{gid}/stripe-onboarding-refresh")
def driver_stripe_onboarding_refresh(gid: str, db: Session = Depends(get_db)):
    """refresh_url do AccountLink: o link expirou ou foi reaberto — gera um novo e redireciona."""
    driver = _get_driver_by_gid_or_404(gid, db)
    if not driver.stripe_account_id:
        return _partner_page("Link expirado", "Volte ao app e toque em \"Configurar pagamentos\".")
    try:
        return RedirectResponse(_onboarding_link(driver))
    except Exception:
        return _partner_page("Link expirado", "Volte ao app e toque em \"Configurar pagamentos\".")


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
    _apply_profile(driver, payload.personal_info, payload.vehicle_info)
    db.commit()
    db.refresh(driver)
    return driver


@router.get("/orders", response_model=List[dict])
def get_available_orders(driver_id: str, db: Session = Depends(get_db)):
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
def accept_order(sub_order_id: int, driver_id: str, db: Session = Depends(get_db)):
    driver = _get_driver_or_404(driver_id, db)
    sub = db.query(SubOrderDB).filter(SubOrderDB.id == sub_order_id, SubOrderDB.driver_gid == driver.gid).first()
    if not sub: raise HTTPException(status_code=404, detail="Sub-pedido não encontrado ou não atribuído.")
    sub.status = "A aguardar estafeta"
    db.commit()
    return {"message": "Aceite."}

@router.post("/{sub_order_id}/reject")
def reject_order(sub_order_id: int, driver_id: str, db: Session = Depends(get_db)):
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


def _ensure_stop_not_cancelled(stop: RouteStopDB) -> None:
    if is_sub_order_cancelled(stop.sub_order):
        raise HTTPException(status_code=409, detail="Este pedido foi cancelado — não precisa de o recolher.")


def _normalize_code(code: Optional[str]) -> str:
    return "".join((code or "").split()).upper()


@router.get("/routes", response_model=List[dict])
def get_driver_routes(driver_id: str, db: Session = Depends(get_db)):
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
    if close_cancelled_routes(db, routes):
        routes = [r for r in routes if r.status != "CANCELLED"]
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
                "status": sub.status if sub else None,
                "ready_at_estimated": stop.ready_at_estimated,
                # stop.ready_at_confirmed é a cópia do momento da oferta; o "Pedido pronto"
                # carregado depois só fica em sub.ready_at (POST /orders/{id}/ready).
                "ready_at_confirmed": stop.ready_at_confirmed or (sub.ready_at if sub else None),
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
            # tracking_code fica de fora de propósito: é o código que o cliente mostra ao
            # estafeta para provar a entrega (ver complete_route).
            "stops": stops,
        })
    return result


@router.post("/routes/{route_gid}/accept")
def accept_route(route_gid: str, driver_id: str, db: Session = Depends(get_db)):
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
def reject_route(route_gid: str, driver_id: str, db: Session = Depends(get_db)):
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
def mark_stop_arrived(route_gid: str, sub_order_id: int, driver_id: str, db: Session = Depends(get_db)):
    driver = _get_driver_or_404(driver_id, db)
    route = _get_driver_route_or_404(route_gid, driver, db)
    stop = _get_route_stop_or_404(route, sub_order_id, db)
    _ensure_stop_not_cancelled(stop)

    stop.arrived_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "Chegada registada.", "sub_order_id": sub_order_id}


@router.post("/routes/{route_gid}/stops/{sub_order_id}/picked-up")
def mark_stop_picked_up(route_gid: str, sub_order_id: int, driver_id: str, db: Session = Depends(get_db)):
    driver = _get_driver_or_404(driver_id, db)
    route = _get_driver_route_or_404(route_gid, driver, db)
    stop = _get_route_stop_or_404(route, sub_order_id, db)
    _ensure_stop_not_cancelled(stop)

    stop.picked_up_at = datetime.now(timezone.utc)
    if stop.sub_order:
        stop.sub_order.status = "A caminho"
    if route.status == "ACCEPTED":
        route.status = "IN_PROGRESS"
    db.commit()
    return {"message": "Recolha registada.", "sub_order_id": sub_order_id}


@router.post("/routes/{route_gid}/complete")
def complete_route(
    route_gid: str,
    driver_id: str,
    payload: Optional[RouteCompleteRequest] = None,
    db: Session = Depends(get_db),
):
    """
    Conclui a entrega. Exige o código que o cliente mostra no app Koma (tracking_code do
    pedido); pedidos antigos sem código gravado não o exigem. Paragens canceladas ficam
    como estão — não podem passar a "Entregue".
    """
    driver = _get_driver_or_404(driver_id, db)
    route = _get_driver_route_or_404(route_gid, driver, db)
    close_cancelled_routes(db, [route])
    if route.status not in ("ACCEPTED", "IN_PROGRESS"):
        raise HTTPException(status_code=409, detail=f"Rota não pode ser concluída (status atual: {route.status}).")

    active_stops = [s for s in route.stops if s.sub_order and not is_sub_order_cancelled(s.sub_order)]
    if any(s.picked_up_at is None for s in active_stops):
        raise HTTPException(status_code=409, detail="Ainda há recolhas por fazer nesta rota.")

    master = route.master_order
    expected_code = _normalize_code(master.tracking_code if master else None)
    if expected_code and _normalize_code(payload.code if payload else None) != expected_code:
        raise HTTPException(status_code=400, detail="Código de entrega inválido. Peça ao cliente o código do pedido.")

    now = datetime.now(timezone.utc)
    route.status = "COMPLETED"
    route.completed_at = now
    for stop in active_stops:
        stop.sub_order.status = "Entregue"

    if master and all(s.status == "Entregue" or is_sub_order_cancelled(s) for s in master.sub_orders):
        master.status = "Entregue"

    db.commit()

    # Melhor esforço: a entrega já está registada; se o pagamento falhar fica pendente e o
    # worker de despacho volta a tentar (services/driver_payout_service.py).
    pay_driver_for_route(db, route)
    return {"message": "Entrega concluída.", "route_gid": route_gid}


@router.post("/location")
def update_location(driver_id: str, payload: DriverLocationUpdate, db: Session = Depends(get_db)):
    driver = _get_driver_or_404(driver_id, db)
    driver.latitude = payload.latitude
    driver.longitude = payload.longitude
    driver.last_seen = datetime.now(timezone.utc)
    db.commit()
    return {"status": "ok"}


@router.post("/{driver_id}/fcm-token")
def update_fcm_token(driver_id: str, payload: DriverFcmTokenUpdate, db: Session = Depends(get_db)):
    """
    PLANO_RECOLHA_MULTI_RESTAURANTE.md, Fase 5 — registra/atualiza o token FCM do
    dispositivo do estafeta, chamado pelo app após o login e sempre que o token for
    renovado. Sem chamar isto, o estafeta continua recebendo ofertas normalmente — só
    via polling, sem o aviso instantâneo do push (ver services/push_notification_service.py).
    """
    driver = _get_driver_or_404(driver_id, db)
    driver.fcm_token = payload.token
    db.commit()
    return {"status": "ok"}


@router.get("/{gid}", response_model=DriverProfileResponse)
def get_driver_profile(gid: str, db: Session = Depends(get_db)):
    """GET /drivers/{gid} — o app usa para saber se a conta já ficou ativa."""
    return _get_driver_by_gid_or_404(gid, db)
