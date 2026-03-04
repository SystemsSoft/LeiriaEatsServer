# Arquivo: api/routes/order_routes.py
from typing import List

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy.orm import Session

from core import config
from core.database import get_db, SessionLocal
from core.sql_models import OrderDB, OrderItemDB, ProductDB, RestaurantDB, SavedPaymentMethodDB
from schemas.models import OrderCreate, OrderResponse, OrderStatusUpdate

router = APIRouter()


@router.post("/orders/initiate-checkout")
def initiate_order_and_create_checkout_session(order_data: OrderCreate, db: Session = Depends(get_db)):
    """
    Passo 1: Recebe os dados do carrinho, cria um pedido com status PENDENTE
    e gera a URL de pagamento do Stripe.
    """
    valid_items = []
    total_price = 0.0
    for item in order_data.items:
        product = db.query(ProductDB).filter(ProductDB.id == item.product_id).first()
        if product:
            total_price += product.price * item.quantity
        else:
            raise HTTPException(status_code=404, detail=f"Produto com id {item.product_id} não encontrado")

        valid_items.append((product, item))

    stripe_customer_id = None
    if order_data.save_payment_method:
        existing_saved_method = db.query(SavedPaymentMethodDB).filter(
            SavedPaymentMethodDB.user_id == order_data.user_id
        ).order_by(SavedPaymentMethodDB.id.desc()).first()

        if existing_saved_method:
            stripe_customer_id = existing_saved_method.stripe_customer_id
        else:
            try:
                customer = stripe.Customer.create(
                    name=order_data.user_name,
                    phone=order_data.user_phone,
                    metadata={"user_id": order_data.user_id}
                )
                stripe_customer_id = customer.id
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Erro ao criar cliente Stripe: {str(e)}")

    # 2. Criar o Pedido no Banco com status PENDENTE
    new_order = OrderDB(
        customer_name=order_data.user_name,
        delivery_address=order_data.user_address,
        status="Pendente",
        total=total_price,
        restaurant_id=order_data.restaurant_id,
        user_id=order_data.user_id,
        restaurant_name=order_data.restaurant_name,
        restaurant_category=order_data.restaurant_category,
        restaurant_image_url=order_data.restaurant_image_url,
        stripe_customer_id=stripe_customer_id
    )

    db.add(new_order)
    db.commit()
    db.refresh(new_order)

    for product, item_data in valid_items:
        db_item = OrderItemDB(
            order_id=new_order.id,
            product_name=product.name,
            price=product.price,
            quantity=item_data.quantity,
            observation=item_data.observation,
            image_url=product.image_url,
            description=product.description
        )

        db.add(db_item)
    db.commit()

    restaurant = db.query(RestaurantDB).filter(RestaurantDB.id == order_data.restaurant_id).first()
    if not restaurant or not restaurant.stripe_account_id:
        raise HTTPException(status_code=400, detail="Restaurante não configurado para pagamentos.")

    amount_cents = int(total_price * 100)
    platform_fee = int(amount_cents * 0.20)

    try:
        payment_intent_data = {
            'application_fee_amount': platform_fee,
            'transfer_data': {'destination': restaurant.stripe_account_id},
        }

        if order_data.save_payment_method:
            payment_intent_data['setup_future_usage'] = 'off_session'

        checkout_payload = {
            'line_items': [{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {'name': f'Pedido para {restaurant.name}'},
                    'unit_amount': amount_cents,
                },
                'quantity': 1,
            }],
            'mode': 'payment',
            'success_url': f'http://localhost/success?order_id={new_order.id}',
            'cancel_url': 'http://localhost/cancel',
            'payment_intent_data': payment_intent_data,
            'metadata': {
                'order_id': str(new_order.id),
                'user_id': order_data.user_id,
                'save_payment_method': str(order_data.save_payment_method).lower(),
            }
        }

        if stripe_customer_id:
            checkout_payload['customer'] = stripe_customer_id

        checkout_session = stripe.checkout.Session.create(**checkout_payload)

        # Retorna apenas a URL para o app
        return {"url": checkout_session.url}

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))




@router.get("/orders/customer/{user_id}", response_model=List[OrderResponse])
def get_customer_orders(user_id: str, db: Session = Depends(get_db)):
    print(f"👤 Buscando histórico de: {user_id}")

    orders = db.query(OrderDB).filter_by(
        user_id=user_id
    ).order_by(OrderDB.id.desc()).all()

    return orders


@router.get("/orders/{restaurant_id}", response_model=List[OrderResponse])
def get_restaurant_orders(restaurant_id: int, db: Session = Depends(get_db)):
    print(f"🔎 Buscando pedidos para o Restaurante ID {restaurant_id}")

    # Busca no banco filtrando pelo ID do restaurante
    orders = db.query(OrderDB).filter(
        OrderDB.restaurant_id == restaurant_id
    ).order_by(OrderDB.id.desc()).all()

    return orders


@router.put("/orders/{order_id}/status")
def update_order_status(order_id: int, status_data: OrderStatusUpdate, db: Session = Depends(get_db)):
    print(f"🔄 Atualizando pedido #{order_id} para: {status_data.status}")

    order = db.query(OrderDB).filter(OrderDB.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    if status_data.status == "Cancelado":
        if order.payment_intent_id and order.status != "Cancelado":
            try:
                print(f"💸 Iniciando estorno na Stripe para: {order.payment_intent_id}")
                stripe.Refund.create(
                    payment_intent=order.payment_intent_id,
                )
                print("✅ Estorno realizado com sucesso na Stripe!")
            except stripe.error.StripeError as e:
                print(f"❌ Erro ao estornar: {e}")
                raise HTTPException(status_code=400, detail=f"Erro ao processar reembolso: {str(e)}")

    order.status = status_data.status
    db.commit()

    return {"message": "Status atualizado", "status": order.status}


@router.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    payload = await request.body()

    webhook_secret = config.Settings.STRIPE_WEBHOOK_SECRET or config.Settings.STRIPE_API_KEY

    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Cabeçalho Stripe-Signature ausente")

    try:
        # Verifica se o evento veio realmente do Stripe
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=stripe_signature,
            secret=webhook_secret
        )
    except ValueError as e:
        # Payload inválido
        raise HTTPException(status_code=400, detail=str(e))
    except stripe.error.SignatureVerificationError as e:
        # Assinatura inválida
        raise HTTPException(status_code=400, detail=str(e))

    # --- Processa o evento que nos interessa ---
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']

        payment_intent_id = session.get('payment_intent')
        metadata = session.get('metadata', {}) or {}
        order_id = metadata.get('order_id')
        user_id = metadata.get('user_id')
        should_save_payment_method = str(metadata.get('save_payment_method', 'false')).lower() == 'true'

        db = SessionLocal()
        try:
            if order_id and payment_intent_id:
                db_order = db.query(OrderDB).filter(OrderDB.id == int(order_id)).first()
                if db_order:
                    db_order.payment_intent_id = payment_intent_id
                    if session.get('customer'):
                        db_order.stripe_customer_id = session.get('customer')

                    if db_order.status == "PENDING_PAYMENT":
                        db_order.status = "Pendente"

            if should_save_payment_method and user_id and payment_intent_id:
                payment_intent = stripe.PaymentIntent.retrieve(
                    payment_intent_id,
                    expand=['payment_method']
                )
                payment_method = payment_intent.get('payment_method')

                if payment_method and payment_method.get('type') == 'card':
                    card_data = payment_method.get('card', {}) or {}
                    payment_method_id = payment_method.get('id')
                    stripe_customer_id = session.get('customer') or payment_intent.get('customer')

                    if payment_method_id and stripe_customer_id:
                        existing_method = db.query(SavedPaymentMethodDB).filter(
                            SavedPaymentMethodDB.stripe_payment_method_id == payment_method_id
                        ).first()

                        if existing_method:
                            existing_method.user_id = user_id
                            existing_method.stripe_customer_id = stripe_customer_id
                            existing_method.card_brand = card_data.get('brand')
                            existing_method.card_last4 = card_data.get('last4')
                            existing_method.card_exp_month = card_data.get('exp_month')
                            existing_method.card_exp_year = card_data.get('exp_year')
                        else:
                            db.add(SavedPaymentMethodDB(
                                user_id=user_id,
                                stripe_customer_id=stripe_customer_id,
                                stripe_payment_method_id=payment_method_id,
                                card_brand=card_data.get('brand'),
                                card_last4=card_data.get('last4'),
                                card_exp_month=card_data.get('exp_month'),
                                card_exp_year=card_data.get('exp_year')
                            ))

            db.commit()
        finally:
            db.close()

    # Avisa ao Stripe que recebemos o evento com sucesso
    return {"status": "success"}


@router.get("/restaurant/{restaurant_id}/finance-summary")
def get_restaurant_finance_summary(restaurant_id: int, db: Session = Depends(get_db)):
    print(f"💰 Buscando resumo financeiro para o Restaurante ID: {restaurant_id}")

    restaurant = db.query(RestaurantDB).filter(RestaurantDB.id == restaurant_id).first()

    if not restaurant or not restaurant.stripe_account_id:
        raise HTTPException(status_code=404, detail="Restaurante não configurado para pagamentos.")

    try:
        # 1. Busca os saldos atuais
        balance = stripe.Balance.retrieve(stripe_account=restaurant.stripe_account_id)

        # 2. Busca os repasses futuros/pendentes (limitamos a 3 para mostrar na lista)
        upcoming_payouts = stripe.Payout.list(
            limit=3,
            status="pending",  # Traz apenas os que ainda vão cair
            stripe_account=restaurant.stripe_account_id
        )

        # 3. NOVO: Busca os repasses que JÁ FORAM PAGOS (Já caíram no banco)
        paid_payouts = stripe.Payout.list(
            limit=100,  # Puxa até os últimos 100 repasses realizados
            status="paid",
            stripe_account=restaurant.stripe_account_id
        )

        # 4. Faz as somas convertendo de centavos para Euros
        available = sum(b.amount for b in balance.available) / 100.0
        pending = sum(b.amount for b in balance.pending) / 100.0

        # Faz a soma de todo o dinheiro que já foi transferido para o banco
        total_ja_repassado = sum(p.amount for p in paid_payouts.data) / 100.0

        # Formata a lista dos próximos repasses para o Flutter
        upcoming_list = [
            {
                "amount": p.amount / 100.0,
                "status": p.status,
                "expected_arrival_date": p.arrival_date
            } for p in upcoming_payouts.data
        ]

        return {
            "saldo_disponivel_eur": available,
            "saldo_pendente_eur": pending,
            "total_ja_repassado_eur": total_ja_repassado,  # <--- NOVO CAMPO AQUI
            "proximos_repasses": upcoming_list
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

