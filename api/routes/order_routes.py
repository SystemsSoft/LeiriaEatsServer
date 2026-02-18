# Arquivo: api/routes/order_routes.py
from typing import List

import stripe
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core import config
from core.database import get_db
from core.sql_models import OrderDB, OrderItemDB, ProductDB, RestaurantDB
from schemas.models import OrderCreate, OrderResponse, OrderStatusUpdate

router = APIRouter()


@router.post("/orders/initiate-checkout")
def initiate_order_and_create_checkout_session(order_data: OrderCreate, db: Session = Depends(get_db)):
    """
    Passo 1: Recebe os dados do carrinho, cria um pedido com status PENDENTE
    e gera a URL de pagamento do Stripe.
    """

    # --- Parte da Lógica do 'create_order' ---
    # 1. Validar produtos e calcular o total
    total_price = 0.0
    for item in order_data.items:
        product = db.query(ProductDB).filter(ProductDB.id == item.product_id).first()
        if product:
            total_price += product.price * item.quantity
        else:
            raise HTTPException(status_code=404, detail=f"Produto com id {item.product_id} não encontrado")

    # 2. Criar o Pedido no Banco com status PENDENTE
    new_order = OrderDB(
        customer_name=order_data.user_name,
        delivery_address=order_data.user_address,
        status="pendente",
        total=total_price,
        restaurant_id=order_data.restaurant_id,
        user_id=order_data.user_id,
        restaurant_name=order_data.restaurant_name
    )
    db.add(new_order)
    db.commit()
    db.refresh(new_order)


    restaurant = db.query(RestaurantDB).filter(RestaurantDB.id == order_data.restaurant_id).first()
    if not restaurant or not restaurant.stripe_account_id:
        raise HTTPException(status_code=400, detail="Restaurante não configurado para pagamentos.")

    amount_cents = int(total_price * 100)
    platform_fee = int(amount_cents * 0.20)

    try:
        checkout_session = stripe.checkout.Session.create(
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {'name': f'Pedido para {restaurant.name}'},
                    'unit_amount': amount_cents,
                },
                'quantity': 1,
            }],
            mode='payment',
            # CRUCIAL: Passamos o ID do nosso pedido para a URL de sucesso
            success_url=f'http://localhost/success?order_id={new_order.id}',
            cancel_url='http://localhost/cancel',
            payment_intent_data={
                'application_fee_amount': platform_fee,
                'transfer_data': {'destination': restaurant.stripe_account_id},
            },
            # Opcional, mas bom para referência:
            metadata={'order_id': new_order.id}
        )

        # Retorna apenas a URL para o app
        return {"url": checkout_session.url}

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))




@router.get("/orders/customer/{user_id}", response_model=List[OrderResponse])
def get_customer_orders(user_id: str, db: Session = Depends(get_db)):
    print(f"👤 Buscando histórico de: {user_id}")


    # Use filter_by para evitar o erro de tipo e simplificar a sintaxe
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


from fastapi import Request, Header


@router.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    payload = await request.body()


    try:
        # Verifica se o evento veio realmente do Stripe
        event = stripe.Webhook.construct_event(
            payload=payload, sig_header=stripe_signature, secret=config.Settings.STRIPE_API_KEY
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

        # 1. CAPTURA O ID DO PAGAMENTO
        payment_intent_id = session.get('payment_intent')

        # 2. CAPTURA O ID DO NOSSO PEDIDO QUE GUARDAMOS NO METADATA
        order_id = session.get('metadata', {}).get('order_id')

        if order_id and payment_intent_id:
            db = next(get_db())  # Obtém uma sessão do banco
            db_order = db.query(OrderDB).filter(OrderDB.id == int(order_id)).first()

            if db_order and db_order.status == "PENDING_PAYMENT":
                # 3. ATUALIZA O PEDIDO NO BANCO
                db_order.status = "Pendente"  # Ou "Pago"
                db_order.payment_intent_id = payment_intent_id
                db.commit()
                print(f"Pedido {order_id} atualizado com sucesso para pago!")

    # Avisa ao Stripe que recebemos o evento com sucesso
    return {"status": "success"}



