# Arquivo: api/routes/company_routes.py
import stripe
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

# Importações do seu projeto
from core.database import get_db
from core.config import settings
from core.sql_models import RestaurantDB
from repositories.restaurant_repo import RestaurantRepository
from schemas.company import CompanyResponse, CompanyCreateRequest, CompanyUpdateRequest

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


# ==========================================
# 💸 ROTAS DE PAGAMENTO (CHECKOUT)
# ==========================================

@router.post("/checkout/create-intent")
def create_payment_intent(amount_euros: float, restaurant_id: int, db: Session = Depends(get_db)):
    """
    Cria a intenção de pagamento dividindo o dinheiro:
    - 80% para o Restaurante
    - 20% para o Leiria Eats (Platform Fee)
    """
    restaurant = db.query(RestaurantDB).filter(RestaurantDB.id == restaurant_id).first()

    if not restaurant or not restaurant.stripe_account_id:
        raise HTTPException(status_code=400, detail="Restaurante não configurou pagamentos.")

    # Converte para Centavos
    amount_cents = int(amount_euros * 100)

    # Calcula comissão (20%)
    platform_fee = int(amount_cents * 0.20)

    try:
        intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency="eur",
            payment_method_types=["card"],

            # SPLIT AUTOMÁTICO
            application_fee_amount=platform_fee,
            transfer_data={
                "destination": restaurant.stripe_account_id,
            },
        )

        return {"client_secret": intent.client_secret, "id": intent.id}

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))