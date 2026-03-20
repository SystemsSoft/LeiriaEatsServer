# Arquivo: api/routes/order_routes.py
from typing import List, Dict, Any

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy.orm import Session

from core import config
from core.database import get_db, SessionLocal
from core.sql_models import OrderDB, OrderItemDB, ProductDB, RestaurantDB, SavedPaymentMethodDB, ProductRatingDB
from schemas.models import OrderCreate, OrderResponse, OrderStatusUpdate, RatingRequest

router = APIRouter()

# Garante que chamadas Stripe nesse módulo usem a chave secreta do backend
stripe.api_key = config.settings.STRIPE_API_KEY


def get_commission_rate(plan: str | None) -> float:
    """Retorna a taxa de comissão com base no plano do restaurante.
    - ESSENCE → 18%
    - SMART   → 21%
    """
    if plan and plan.upper() == "SMART":
        return 0.21
    return 0.18  # ESSENCE é o padrão


def _try_automatic_payment_with_saved_card(
    *,
    db: Session,
    new_order: OrderDB,
    saved_method: SavedPaymentMethodDB,
    restaurant: RestaurantDB,
    amount_cents: int,
    platform_fee: int,
):
    """
    Tenta cobrar off-session usando o último cartão salvo do usuário.
    Retorna um dict com dados do pagamento quando sucesso, ou None para fallback em Checkout.
    """
    if not saved_method:
        return None

    if not saved_method.stripe_customer_id or not saved_method.stripe_payment_method_id:
        return None

    try:
        # Garantir que o payment_method está anexado ao customer antes de cobrar
        try:
            stripe.PaymentMethod.attach(
                saved_method.stripe_payment_method_id,
                customer=saved_method.stripe_customer_id
            )
        except stripe.error.StripeError:
            # Já está anexado, ignora silenciosamente
            pass

        payment_intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency="eur",
            customer=saved_method.stripe_customer_id,
            payment_method=saved_method.stripe_payment_method_id,
            off_session=True,
            confirm=True,
            application_fee_amount=platform_fee,
            transfer_data={"destination": restaurant.stripe_account_id},
            metadata={
                "order_id": str(new_order.id),
                "user_id": new_order.user_id,
                "payment_flow": "off_session_saved_card",
            },
        )

        new_order.payment_intent_id = payment_intent.id
        new_order.stripe_customer_id = saved_method.stripe_customer_id
        new_order.status = "Pendente"
        db.commit()

        return {
            "url": None,
            "auto_paid": True,
            "order_id": new_order.id,
            "payment_intent_id": payment_intent.id,
            "status": new_order.status,
        }

    except stripe.error.CardError as e:
        # Falhas de autenticação/recusa caem para fluxo de Checkout com UI.
        print(f"⚠️ Falha no pagamento automático para pedido {new_order.id}: {str(e)}")
        return None
    except stripe.error.StripeError as e:
        print(f"⚠️ Erro Stripe no pagamento automático para pedido {new_order.id}: {str(e)}")
        return None


@router.post("/orders/initiate-checkout")
def initiate_order_and_create_checkout_session(order_data: OrderCreate, db: Session = Depends(get_db)):
    """
    Cria pedido com status de pagamento pendente.
    - Se houver cartão salvo e save_payment_method=true, tenta cobrança automática off-session.
    - Se não for possível cobrar automaticamente, cria Checkout Session (fallback).
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

    existing_saved_method = None
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

    new_order = OrderDB(
        customer_name=order_data.user_name,
        delivery_address=order_data.user_address,
        status="PENDING_PAYMENT",
        total=total_price,
        restaurant_id=order_data.restaurant_id,
        user_id=order_data.user_id,
        restaurant_name=order_data.restaurant_name,
        restaurant_category=order_data.restaurant_category,
        restaurant_image_url=order_data.restaurant_image_url,
        stripe_customer_id=stripe_customer_id,
        tracking_code=order_data.tracking_code,
        delivery_type=order_data.delivery_type,
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
    commission_rate = get_commission_rate(restaurant.plan)
    platform_fee = int(amount_cents * commission_rate)
    # Tenta cobrança automática apenas se houver cartão salvo válido
    if (order_data.save_payment_method and
        existing_saved_method is not None and
        existing_saved_method.stripe_customer_id and
        restaurant is not None):
        auto_payment_result = _try_automatic_payment_with_saved_card(
            db=db,
            new_order=new_order,
            saved_method=existing_saved_method,
            restaurant=restaurant,
            amount_cents=amount_cents,
            platform_fee=platform_fee,
        )
        if auto_payment_result:
            return auto_payment_result

    try:
        payment_intent_data: Dict[str, Any] = {
            "application_fee_amount": platform_fee,
            "transfer_data": {"destination": restaurant.stripe_account_id},
        }

        if order_data.save_payment_method:
            payment_intent_data["setup_future_usage"] = "off_session"

        checkout_payload = {
            "line_items": [{
                "price_data": {
                    "currency": "eur",
                    "product_data": {"name": f"Pedido para {restaurant.name}"},
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            "mode": "payment",
            "success_url": f"http://localhost/success?order_id={new_order.id}",
            "cancel_url": "http://localhost/cancel",
            "payment_intent_data": payment_intent_data,
            "metadata": {
                "order_id": str(new_order.id),
                "user_id": order_data.user_id,
                "save_payment_method": str(order_data.save_payment_method).lower(),
            }
        }

        if stripe_customer_id:
            checkout_payload["customer"] = stripe_customer_id

        checkout_session = stripe.checkout.Session.create(**checkout_payload)

        return {
            "url": checkout_session.url,
            "auto_paid": False,
            "order_id": new_order.id,
        }

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

    print(f"🔔 Webhook recebido. Signature: {stripe_signature[:20] if stripe_signature else 'None'}...")

    webhook_secret = config.settings.STRIPE_WEBHOOK_SECRET or config.settings.STRIPE_API_KEY

    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Cabeçalho Stripe-Signature ausente")

    try:
        # Verifica se o evento veio realmente do Stripe
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=stripe_signature,
            secret=webhook_secret
        )
        print(f"✅ Evento validado: {event['type']}")
    except ValueError as e:
        # Payload inválido
        print(f"❌ Erro - Payload inválido: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except stripe.error.SignatureVerificationError as e:
        # Assinatura inválida
        print(f"❌ Erro - Assinatura inválida: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

    # --- Processa o evento que nos interessa ---
    if event['type'] == 'checkout.session.completed':
        print(f"🎉 Evento checkout.session.completed recebido!")
        session = event['data']['object']

        payment_intent_id = session.get('payment_intent')
        metadata = session.get('metadata', {}) or {}
        order_id = metadata.get('order_id')
        user_id = metadata.get('user_id')
        should_save_payment_method = str(metadata.get('save_payment_method', 'false')).lower() == 'true'

        print(f"📋 Order ID: {order_id}, User ID: {user_id}, PaymentIntent: {payment_intent_id}")

        db = SessionLocal()
        try:
            if order_id and payment_intent_id:
                db_order = db.query(OrderDB).filter(OrderDB.id == int(order_id)).first()
                if db_order:
                    print(f"📦 Pedido encontrado: {db_order.id}, Status anterior: {db_order.status}")
                    db_order.payment_intent_id = payment_intent_id
                    if session.get('customer'):
                        db_order.stripe_customer_id = session.get('customer')

                    if db_order.status == "PENDING_PAYMENT":
                        db_order.status = "Pendente"
                        print(f"✅ Status atualizado para: Pendente")
                else:
                    print(f"❌ Pedido {order_id} não encontrado no banco!")

            if should_save_payment_method and user_id and payment_intent_id:
                print(f"💾 Salvando método de pagamento para user: {user_id}")
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
                            print(f"🔄 Atualizando método existente: {payment_method_id}")
                            existing_method.user_id = user_id
                            existing_method.stripe_customer_id = stripe_customer_id
                            existing_method.card_brand = card_data.get('brand')
                            existing_method.card_last4 = card_data.get('last4')
                            existing_method.card_exp_month = card_data.get('exp_month')
                            existing_method.card_exp_year = card_data.get('exp_year')
                        else:
                            print(f"➕ Criando novo método salvo: {payment_method_id}")
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
            print(f"✅ Webhook processado com sucesso!")
        except Exception as e:
            print(f"❌ Erro ao processar webhook: {str(e)}")
            db.rollback()
        finally:
            db.close()

    # Avisa ao Stripe que recebemos o evento com sucesso
    return {"status": "success"}


@router.get("/users/{user_id}/saved-payment-methods")
def get_user_saved_payment_methods(user_id: str, db: Session = Depends(get_db)):
    """
    Retorna todos os métodos de pagamento salvos do usuário.
    Útil para o app verificar antes de tentar pagamento automático.
    """
    print(f"💳 Buscando métodos de pagamento salvos para user: {user_id}")

    saved_methods = db.query(SavedPaymentMethodDB).filter(
        SavedPaymentMethodDB.user_id == user_id
    ).all()

    if not saved_methods:
        return {
            "has_saved_methods": False,
            "methods": []
        }

    methods_data = [
        {
            "id": method.id,
            "brand": method.card_brand,
            "last4": method.card_last4,
            "exp_month": method.card_exp_month,
            "exp_year": method.card_exp_year,
            "stripe_payment_method_id": method.stripe_payment_method_id,
        }
        for method in saved_methods
    ]

    return {
        "has_saved_methods": True,
        "methods": methods_data
    }


@router.delete("/users/{user_id}/saved-payment-methods/{method_id}")
def delete_saved_payment_method(user_id: str, method_id: int, db: Session = Depends(get_db)):
    """
    Deleta um método de pagamento salvo do usuário.
    """
    print(f"🗑️ Deletando método {method_id} do user: {user_id}")

    saved_method = db.query(SavedPaymentMethodDB).filter(
        SavedPaymentMethodDB.id == method_id,
        SavedPaymentMethodDB.user_id == user_id
    ).first()

    if not saved_method:
        raise HTTPException(status_code=404, detail="Método de pagamento não encontrado")

    try:
        # Detacha o método de pagamento do Stripe
        stripe.PaymentMethod.detach(saved_method.stripe_payment_method_id)
        print(f"✅ PaymentMethod {saved_method.stripe_payment_method_id} desanexado do Stripe")
    except stripe.error.StripeError as e:
        print(f"⚠️ Aviso ao desanexar no Stripe: {str(e)}")
        # Continua mesmo se falhar no Stripe, pois pode já estar deletado

    db.delete(saved_method)
    db.commit()

    return {"message": "Método de pagamento deletado com sucesso"}


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


@router.post("/orders/ratings")
def submit_order_ratings(payload: RatingRequest, db: Session = Depends(get_db)):
    """
    Recebe as avaliações dos produtos de um pedido.
    Calcula e atualiza o rating médio de cada produto avaliado.
    """
    order_id_int = int(payload.order_id)

    # Valida o pedido
    order = db.query(OrderDB).filter(OrderDB.id == order_id_int).first()
    if not order:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    if order.restaurant_id != payload.restaurant_id:
        raise HTTPException(status_code=400, detail="restaurant_id não corresponde ao pedido")

    saved_ratings = []
    for item in payload.ratings:
        if not (1 <= item.rating <= 5):
            raise HTTPException(
                status_code=422,
                detail=f"Rating inválido ({item.rating}) para produto {item.product_id}. Deve ser entre 1 e 5."
            )

        product = db.query(ProductDB).filter(ProductDB.id == item.product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail=f"Produto {item.product_id} não encontrado")

        # Evita duplicatas por pedido+produto (upsert manual)
        existing = db.query(ProductRatingDB).filter(
            ProductRatingDB.order_id == order_id_int,
            ProductRatingDB.product_id == item.product_id,
        ).first()

        if existing:
            existing.rating = item.rating
        else:
            new_rating = ProductRatingDB(
                order_id=order_id_int,
                product_id=item.product_id,
                restaurant_id=payload.restaurant_id,
                rating=item.rating,
            )
            db.add(new_rating)

        saved_ratings.append(item.product_id)

    db.commit()

    # Recalcula o rating médio de cada produto avaliado e persiste no ProductDB
    for product_id in saved_ratings:
        all_ratings = db.query(ProductRatingDB).filter(
            ProductRatingDB.product_id == product_id,
            ProductRatingDB.restaurant_id == payload.restaurant_id
        ).all()
        if all_ratings:
            avg = sum(r.rating for r in all_ratings) / len(all_ratings)
            product = db.query(ProductDB).filter(ProductDB.id == product_id).first()
            if product:
                product.rating = round(avg, 2)

    db.commit()

    print(f"✅ Avaliações registadas para o pedido {order_id_int}: produtos {saved_ratings}")
    return {"message": "Avaliações registadas com sucesso", "rated_products": saved_ratings}

