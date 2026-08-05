# Arquivo: api/routes/company_routes.py
import stripe
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List

# Importações do seu projeto
from core.database import get_db
from core.config import settings
from core.sql_models import RestaurantDB, RestaurantHourDB, DeliveryZoneDB
from repositories.restaurant_repo import RestaurantRepository
from schemas.company import CompanyResponse, CompanyCreateRequest, CompanyUpdateRequest, RestaurantHourRequest, RestaurantHourResponse, UsesPlatformCourierRequest, DeliveryZoneRequest, DeliveryZoneResponse
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
            restaurant.status = "STRIPE_PENDING"  # Conta Stripe criada, aguarda onboarding
            db.commit()
            print(f"✅ Conta Stripe Criada: {account.id} → status=STRIPE_PENDING")

        # 3. Gera o Link Mágico com URLs de produção (incluindo restaurant_id)
        account_link = stripe.AccountLink.create(
            account=restaurant.stripe_account_id,
            refresh_url=f"https://api.leiriaeats.com/connect/onboarding-refresh?restaurant_id={restaurant_id}",
            return_url=f"https://api.leiriaeats.com/connect/onboarding-success?restaurant_id={restaurant_id}",
            type="account_onboarding",
        )

        return {
            "url": account_link.url,
            "stripe_account_id": restaurant.stripe_account_id,
            "status": restaurant.status,
            "message": "Complete o onboarding no Stripe. O status mudará para ACTIVE automaticamente após conclusão."
        }

    except Exception as e:
        print(f"❌ Erro Stripe: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/connect/onboarding-success", response_class=HTMLResponse)
def onboarding_success():
    """
    Endpoint chamado pelo Stripe após o restaurante completar o onboarding.
    O webhook account.updated já atualizará o status para ACTIVE automaticamente.
    """
    html_content = """
    <!DOCTYPE html>
    <html lang="pt">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Onboarding Concluído - Koma Ai</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #D8E5E3 0%, #89C9B8 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }
            
            .container {
                background: white;
                border-radius: 20px;
                padding: 60px 40px;
                max-width: 500px;
                width: 100%;
                text-align: center;
                box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                animation: slideUp 0.5s ease-out;
            }
            
            @keyframes slideUp {
                from {
                    opacity: 0;
                    transform: translateY(30px);
                }
                to {
                    opacity: 1;
                    transform: translateY(0);
                }
            }
            
            .success-icon {
                width: 80px;
                height: 80px;
                background: linear-gradient(135deg, #89C9B8 0%, #5FA794 100%);
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0 auto 30px;
                animation: scaleIn 0.5s ease-out 0.2s both;
            }
            
            @keyframes scaleIn {
                from {
                    transform: scale(0);
                }
                to {
                    transform: scale(1);
                }
            }
            
            .checkmark {
                width: 40px;
                height: 40px;
                border: 4px solid white;
                border-top: none;
                border-right: none;
                transform: rotate(-45deg);
                margin-top: -10px;
            }
            
            h1 {
                color: #2d3748;
                font-size: 32px;
                font-weight: 700;
                margin-bottom: 20px;
            }
            
            .message {
                color: #4a5568;
                font-size: 18px;
                line-height: 1.6;
                margin-bottom: 30px;
            }
            
            .status-badge {
                display: inline-block;
                background: linear-gradient(135deg, #89C9B8 0%, #5FA794 100%);
                color: white;
                padding: 12px 30px;
                border-radius: 25px;
                font-weight: 600;
                font-size: 16px;
                margin-bottom: 30px;
                animation: pulse 2s ease-in-out infinite;
            }
            
            @keyframes pulse {
                0%, 100% {
                    transform: scale(1);
                }
                50% {
                    transform: scale(1.05);
                }
            }
            
            .info-text {
                color: #718096;
                font-size: 14px;
                line-height: 1.6;
                margin-bottom: 30px;
            }
            
            .close-button {
                background: linear-gradient(135deg, #89C9B8 0%, #5FA794 100%);
                color: white;
                border: none;
                padding: 16px 40px;
                border-radius: 10px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: transform 0.2s, box-shadow 0.2s;
                box-shadow: 0 4px 15px rgba(137, 201, 184, 0.4);
            }
            
            .close-button:hover {
                transform: translateY(-2px);
                box-shadow: 0 6px 20px rgba(137, 201, 184, 0.6);
            }
            
            .close-button:active {
                transform: translateY(0);
            }
            
            .footer {
                margin-top: 30px;
                color: #a0aec0;
                font-size: 12px;
            }
        </style>
        <script>
            // Extrai restaurant_id da URL
            const urlParams = new URLSearchParams(window.location.search);
            const restaurantId = urlParams.get('restaurant_id');
            
            // Função para atualizar status automaticamente
            async function updateStatus() {
                if (!restaurantId) {
                    console.error('Restaurant ID não encontrado na URL');
                    return;
                }
                
                try {
                    console.log('🔄 Verificando status do restaurante', restaurantId);
                    
                    const response = await fetch(`/connect/check-status/${restaurantId}`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        }
                    });
                    
                    const data = await response.json();
                    console.log('✅ Status atualizado:', data);
                    
                    if (data.status === 'ACTIVE') {
                        console.log('🎉 Conta ativada com sucesso!');
                    }
                } catch (error) {
                    console.error('❌ Erro ao atualizar status:', error);
                }
            }
            
            // Chama a atualização imediatamente
            updateStatus();
            
            // Auto-redirect para deep link depois de 3 segundos
            setTimeout(function() {
                window.location.href = 'komarestaurant://onboarding-success';
            }, 3000);
            
            function closeWindow() {
                // Tenta fechar a janela/aba
                window.close();
                
                // Se não funcionar (navegadores modernos bloqueiam), redireciona para deep link
                setTimeout(function() {
                    window.location.href = 'komarestaurant://onboarding-success';
                }, 100);
            }
        </script>
    </head>
    <body>
        <div class="container">
            <div class="success-icon">
                <div class="checkmark"></div>
            </div>
            
            <h1>✨ Onboarding Concluído!</h1>
            
            <div class="status-badge">
                🎉 Conta Ativada com Sucesso
            </div>
            
            <p class="message">
                Parabéns! Seu cadastro foi finalizado com sucesso.<br>
                Seu status será atualizado para <strong>ACTIVE</strong> em instantes.
            </p>
            
            <p class="info-text">
                Você já pode começar a usar todas as funcionalidades da plataforma Koma Ai.
            </p>
            
            <button class="close-button" onclick="closeWindow()">
                Fechar esta aba
            </button>
            
            <p class="footer">
                Redirecionando automaticamente em 3 segundos...
            </p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.get("/connect/onboarding-refresh", response_class=HTMLResponse)
def onboarding_refresh():
    """
    Endpoint chamado se o link de onboarding expirar.
    O app deve chamar novamente POST /connect/onboarding/{restaurant_id}
    """
    html_content = """
    <!DOCTYPE html>
    <html lang="pt">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Link Expirado - Koma Ai</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #D8E5E3 0%, #E8B4A8 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }
            
            .container {
                background: white;
                border-radius: 20px;
                padding: 60px 40px;
                max-width: 500px;
                width: 100%;
                text-align: center;
                box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                animation: slideUp 0.5s ease-out;
            }
            
            @keyframes slideUp {
                from {
                    opacity: 0;
                    transform: translateY(30px);
                }
                to {
                    opacity: 1;
                    transform: translateY(0);
                }
            }
            
            .error-icon {
                width: 80px;
                height: 80px;
                background: linear-gradient(135deg, #E8B4A8 0%, #D88A7A 100%);
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0 auto 30px;
                font-size: 48px;
                animation: scaleIn 0.5s ease-out 0.2s both;
            }
            
            @keyframes scaleIn {
                from {
                    transform: scale(0) rotate(-180deg);
                }
                to {
                    transform: scale(1) rotate(0);
                }
            }
            
            h1 {
                color: #2d3748;
                font-size: 32px;
                font-weight: 700;
                margin-bottom: 20px;
            }
            
            .message {
                color: #4a5568;
                font-size: 18px;
                line-height: 1.6;
                margin-bottom: 30px;
            }
            
            .info-text {
                color: #718096;
                font-size: 14px;
                line-height: 1.6;
                margin-bottom: 30px;
                background: #f7fafc;
                padding: 20px;
                border-radius: 10px;
                border-left: 4px solid #D88A7A;
            }
            
            .close-button {
                background: linear-gradient(135deg, #E8B4A8 0%, #D88A7A 100%);
                color: white;
                border: none;
                padding: 16px 40px;
                border-radius: 10px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: transform 0.2s, box-shadow 0.2s;
                box-shadow: 0 4px 15px rgba(232, 180, 168, 0.4);
            }
            
            .close-button:hover {
                transform: translateY(-2px);
                box-shadow: 0 6px 20px rgba(232, 180, 168, 0.6);
            }
            
            .close-button:active {
                transform: translateY(0);
            }
            
            .footer {
                margin-top: 30px;
                color: #a0aec0;
                font-size: 12px;
            }
        </style>
        <script>
            // Auto-redirect para deep link depois de 3 segundos
            setTimeout(function() {
                window.location.href = 'komarestaurant://onboarding-expired';
            }, 3000);
            
            function closeWindow() {
                window.close();
                setTimeout(function() {
                    window.location.href = 'komarestaurant://onboarding-expired';
                }, 100);
            }
        </script>
    </head>
    <body>
        <div class="container">
            <div class="error-icon">
                ⏱️
            </div>
            
            <h1>Link Expirado</h1>
            
            <p class="message">
                O link de onboarding expirou.<br>
                Isso é normal e acontece por segurança.
            </p>
            
            <div class="info-text">
                <strong>O que fazer agora?</strong><br>
                Volte ao aplicativo e solicite um novo link de onboarding.<br>
                Seus dados já foram salvos e você poderá continuar de onde parou.
            </div>
            
            <button class="close-button" onclick="closeWindow()">
                Fechar esta aba
            </button>
            
            <p class="footer">
                Redirecionando automaticamente em 3 segundos...
            </p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.post("/connect/check-status/{restaurant_id}")
def check_stripe_status(restaurant_id: int, db: Session = Depends(get_db)):
    """
    Verifica manualmente o status do onboarding Stripe e atualiza o banco.
    Útil quando o webhook não dispara ou demora muito.
    """
    restaurant = db.query(RestaurantDB).filter(RestaurantDB.id == restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurante não encontrado")

    if not restaurant.stripe_account_id:
        raise HTTPException(status_code=400, detail="Restaurante não tem conta Stripe")

    try:
        # Busca o estado atual da conta no Stripe
        account = stripe.Account.retrieve(restaurant.stripe_account_id)

        details_submitted = getattr(account, "details_submitted", False)
        charges_enabled = getattr(account, "charges_enabled", False)
        payouts_enabled = getattr(account, "payouts_enabled", False)

        is_complete = details_submitted and charges_enabled and payouts_enabled

        print(f"🔍 Verificando conta {restaurant.stripe_account_id}:")
        print(f"   details_submitted: {details_submitted}")
        print(f"   charges_enabled: {charges_enabled}")
        print(f"   payouts_enabled: {payouts_enabled}")
        print(f"   is_complete: {is_complete}")

        # Atualiza o status no banco
        old_status = restaurant.status
        old_license = restaurant.license

        restaurant.stripe_onboarding_completed = is_complete

        if is_complete and restaurant.status != "ACTIVE":
            restaurant.status = "ACTIVE"
            restaurant.license = "ATIVO"
            db.commit()
            print(f"✅ Status atualizado: {old_status} → ACTIVE, {old_license} → ATIVO")
        else:
            db.commit()
            print(f"⚠️ Status mantido: {restaurant.status} (onboarding incomplete)")

        return {
            "restaurant_id": restaurant_id,
            "stripe_account_id": restaurant.stripe_account_id,
            "details_submitted": details_submitted,
            "charges_enabled": charges_enabled,
            "payouts_enabled": payouts_enabled,
            "onboarding_complete": is_complete,
            "status": restaurant.status,
            "license": restaurant.license,
            "updated": is_complete and old_status != "ACTIVE"
        }

    except Exception as e:
        print(f"❌ Erro ao verificar status: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/connect/dashboard/{restaurant_id}")
def get_stripe_dashboard_url(restaurant_id: int, db: Session = Depends(get_db)):
    """
    Gera um link de acesso ao dashboard financeiro da Stripe para o restaurante.
    Verifica o estado real do onboarding na API da Stripe e sincroniza o campo local.
    """
    restaurant = db.query(RestaurantDB).filter(RestaurantDB.id == restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurante não encontrado")

    if not restaurant.stripe_account_id:
        raise HTTPException(status_code=400, detail="Restaurante ainda não tem conta Stripe configurada.")

    try:
        # Verifica o estado real na Stripe (não confia apenas no campo local)
        account = stripe.Account.retrieve(restaurant.stripe_account_id)
        is_complete = (
            getattr(account, "details_submitted", False) and
            getattr(account, "charges_enabled", False) and
            getattr(account, "payouts_enabled", False)
        )

        # Sincroniza o campo na BD
        restaurant.stripe_onboarding_completed = is_complete
        if is_complete and restaurant.status != "ACTIVE":
            restaurant.status = "ACTIVE"  # Onboarding completo → restaurante ativo
            restaurant.license = "ATIVO"  # Sincroniza o campo license
            print(f"✅ Status atualizado para ACTIVE e license para ATIVO (restaurante {restaurant_id})")
        db.commit()

        if not is_complete:
            raise HTTPException(
                status_code=400,
                detail="O onboarding da Stripe ainda não foi concluído. Complete o registo primeiro."
            )

        login_link = stripe.Account.create_login_link(restaurant.stripe_account_id)
        print(f"✅ Dashboard Stripe gerado para restaurante {restaurant_id}")
        return {"url": login_link.url}

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Erro ao gerar dashboard Stripe: {e}")
        import traceback
        traceback.print_exc()
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


