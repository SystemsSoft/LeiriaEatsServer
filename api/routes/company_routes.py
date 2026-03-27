# Arquivo: api/routes/company_routes.py
import stripe
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List

# Importações do seu projeto
from core.database import get_db
from core.config import settings
from core.sql_models import RestaurantDB, RestaurantHourDB
from repositories.restaurant_repo import RestaurantRepository
from schemas.company import CompanyResponse, CompanyCreateRequest, CompanyUpdateRequest, RestaurantHourRequest, RestaurantHourResponse, UsesPlatformCourierRequest
from schemas.payment import PaymentIntentRequest

# --- CONFIGURAÇÃO INICIAL ---
# 1. Cria o Router UMA VEZ SÓ
router = APIRouter()

# 2. Configura a Stripe UMA VEZ SÓ
stripe.api_key = settings.STRIPE_API_KEY


# ==========================================
# 🏢 ROTAS DE GERENCIAMENTO DE EMPRESA
# ==========================================

@router.post("/companies", response_model=CompanyResponse, status_code=201)
def register_company(company_data: CompanyCreateRequest, db: Session = Depends(get_db)):
    """
    Cria uma nova empresa no banco de dados.
    """
    print(f"🏢 Recebendo cadastro: {company_data.name}")
    try:
        # Chama o repositório que faz o Hash da senha e salva
        new_company = RestaurantRepository.create_company(db, company_data)
        print(f"✅ Empresa criada com ID: {new_company.id}")
        return new_company
    except Exception as e:
        print(f"❌ Erro ao criar empresa: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/companies/{company_id}", response_model=CompanyResponse)
def get_company(company_id: int, db: Session = Depends(get_db)):
    db_company = RestaurantRepository.get_by_id(db, company_id)
    if db_company is None:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return db_company


@router.put("/companies/{company_id}", response_model=CompanyResponse)
def update_company(company_id: int, company_update: CompanyUpdateRequest, db: Session = Depends(get_db)):
    db_company = RestaurantRepository.get_by_id(db, company_id)
    if not db_company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    update_data = company_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_company, key, value)

    db.commit()
    db.refresh(db_company)
    return db_company


# ==========================================
# 🔗 ROTAS DO STRIPE CONNECT (ONBOARDING)
# ==========================================

@router.post("/connect/onboarding/{restaurant_id}")
def create_stripe_onboarding(restaurant_id: int, db: Session = Depends(get_db)):
    # 1. Busca o restaurante no banco
    restaurant = db.query(RestaurantDB).filter(RestaurantDB.id == restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurante não encontrado")

    try:
        # 2. Se ele ainda não tem conta Stripe, cria uma
        if not restaurant.stripe_account_id:
            print(f"✨ Criando conta Stripe para {restaurant.name}...")

            # --- CORREÇÃO AQUI 👇 ---
            # Se o login não for um e-mail (não tem @), criamos um falso para o Stripe aceitar
            stripe_email = restaurant.login
            if "@" not in stripe_email:
                stripe_email = f"{restaurant.login}@leiriaeats.com"
            # ------------------------

            account = stripe.Account.create(
                type="express",
                country="PT",
                email=stripe_email,  # Usamos o e-mail corrigido aqui
                capabilities={
                    "card_payments": {"requested": True},
                    "transfers": {"requested": True},
                },
            )

            restaurant.stripe_account_id = account.id
            db.commit()
            print(f"✅ Conta Stripe Criada: {account.id}")

        # 3. Gera o Link Mágico (Mantendo sua porta 8080)
        account_link = stripe.AccountLink.create(
            account=restaurant.stripe_account_id,
            refresh_url="http://localhost:8080/#/",
            return_url="http://localhost:8080/#/sucesso",
            type="account_onboarding",
        )

        return {"url": account_link.url}

    except Exception as e:
        print(f"❌ Erro Stripe: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/checkout/create-session")
def create_checkout_session(request: PaymentIntentRequest, db: Session = Depends(get_db)):

    restaurant = db.query(RestaurantDB).filter(RestaurantDB.id == request.restaurant_id).first()

    if not restaurant or not restaurant.stripe_account_id:
        raise HTTPException(status_code=400, detail="Restaurante não configurou pagamentos.")

    amount_cents = int(request.amount_euros * 100)
    # 15% fixo se o restaurante usa estafeta próprio, senão segue o plano
    if restaurant.use_own_delivery:
        commission_rate = 0.15
    elif restaurant.plan and restaurant.plan.upper() == "SMART":
        commission_rate = 0.21
    else:
        commission_rate = 0.18
    platform_fee = int(amount_cents * commission_rate)

    try:
        checkout_session = stripe.checkout.Session.create(
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {
                        'name': f'Pedido para {restaurant.name}',
                    },
                    'unit_amount': amount_cents,
                },
                'quantity': 1,
            }],
            mode='payment',
            # URLs para as quais o WebView será redirecionado
            success_url='http://localhost/success',
            # Pode ser qualquer URL, o app só vai detectar a palavra "success"
            cancel_url='http://localhost/cancel',
            # Pode ser qualquer URL, o app só vai detectar a palavra "cancel"

            # A mesma lógica de divisão do pagamento que você já tinha
            payment_intent_data={
                'application_fee_amount': platform_fee,
                'transfer_data': {
                    'destination': restaurant.stripe_account_id,
                },
            },
        )

        return {
            "url": checkout_session.url,
            "payment_intent_id": checkout_session.payment_intent
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# 🕐 ROTAS DE HORÁRIOS DE FUNCIONAMENTO
# ==========================================

@router.post("/restaurant/{restaurant_id}/hours", response_model=List[RestaurantHourResponse], status_code=201)
def save_restaurant_hours(
    restaurant_id: int,
    hours: List[RestaurantHourRequest],
    db: Session = Depends(get_db)
):
    """
    Recebe a lista completa de horários semanais do restaurante e
    substitui (upsert) os registos existentes no banco de dados.
    """
    # Valida se o restaurante existe
    restaurant = db.query(RestaurantDB).filter(RestaurantDB.id == restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurante não encontrado")

    print(f"📥 Recebendo {len(hours)} horários para o restaurante {restaurant_id}")

    # Remove todos os horários anteriores deste restaurante (substituição completa)
    db.query(RestaurantHourDB).filter(RestaurantHourDB.restaurant_id == restaurant_id).delete()

    # Insere os novos horários
    new_hours = []
    for h in hours:
        hour_db = RestaurantHourDB(
            restaurant_id=restaurant_id,
            day_of_week=h.day_of_week,
            open_time=h.open_time,
            close_time=h.close_time,
            is_closed=h.is_closed,
        )
        db.add(hour_db)
        new_hours.append(hour_db)

    db.commit()
    for h in new_hours:
        db.refresh(h)

    print(f"✅ {len(new_hours)} horários salvos com sucesso para o restaurante {restaurant_id}")
    return new_hours


@router.get("/restaurant/{restaurant_id}/hours", response_model=List[RestaurantHourResponse])
def get_restaurant_hours(restaurant_id: int, db: Session = Depends(get_db)):
    """
    Retorna os horários de funcionamento do restaurante ordenados por dia.
    """
    restaurant = db.query(RestaurantDB).filter(RestaurantDB.id == restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurante não encontrado")

    hours = (
        db.query(RestaurantHourDB)
        .filter(RestaurantHourDB.restaurant_id == restaurant_id)
        .order_by(RestaurantHourDB.day_of_week)
        .all()
    )
    return hours


# ==========================================
# 🚴 ROTA DE ESTAFETA PRÓPRIO
# ==========================================

@router.get("/restaurant/{restaurant_id}/courier-preference")
def get_courier_preference(
    restaurant_id: int,
    db: Session = Depends(get_db),
):
    """
    Retorna se o restaurante utiliza estafeta próprio ou da plataforma.
    """
    restaurant = db.query(RestaurantDB).filter(RestaurantDB.id == restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurante não encontrado")

    return {
        "restaurant_id": restaurant_id,
        "use_own_delivery": restaurant.use_own_delivery,
    }


@router.patch("/restaurant/{restaurant_id}/courier-preference")
def update_courier_preference(
    restaurant_id: int,
    body: UsesPlatformCourierRequest,
    db: Session = Depends(get_db),
):
    """
    Atualiza se o restaurante utiliza estafeta próprio (True)
    ou os estafetas da plataforma (False).
    """
    restaurant = db.query(RestaurantDB).filter(RestaurantDB.id == restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurante não encontrado")

    restaurant.use_own_delivery = body.use_own_delivery
    db.commit()
    db.refresh(restaurant)

    print(f"✅ Restaurante {restaurant_id} — use_own_delivery={restaurant.use_own_delivery}")
    return {
        "restaurant_id": restaurant_id,
        "use_own_delivery": restaurant.use_own_delivery,
    }


