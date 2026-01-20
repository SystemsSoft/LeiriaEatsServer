# Arquivo: api/routes/order_routes.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from core.database import get_db
from core.sql_models import OrderDB, OrderItemDB, ProductDB
from schemas.models import OrderCreate, OrderResponse, OrderStatusUpdate

router = APIRouter()


@router.post("/orders")
def create_order(order: OrderCreate, db: Session = Depends(get_db)):
    print(f"📦 Recebendo pedido de {order.user_name} para {order.user_address}")

    # 1. Calcular o total e validar produtos
    total_price = 0.0
    valid_items = []

    for item in order.items:
        # Busca o produto no banco
        product = db.query(ProductDB).filter(ProductDB.id == item.product_id).first()

        if product:
            total_price += product.price * item.quantity

            # --- CORREÇÃO FUNDAMENTAL AQUI ---
            # Antes estava: valid_items.append((product, item.quantity))
            # O correto é passar 'item' inteiro para usarmos .quantity e .observation depois
            valid_items.append((product, item))

    if not valid_items:
        raise HTTPException(status_code=400, detail="Carrinho vazio ou produtos inválidos")

    # 2. Criar o Pedido no Banco (Cabeçalho)
    new_order = OrderDB(
        customer_name=order.user_name,
        delivery_address=order.user_address,
        status="Pendente",
        total=total_price,
        restaurant_id=order.restaurant_id
    )
    db.add(new_order)
    db.commit()
    db.refresh(new_order)

    # 3. Salvar os Itens do Pedido
    # Agora 'item_data' é o OBJETO vindo do App, não um int
    for product, item_data in valid_items:
        db_item = OrderItemDB(
            order_id=new_order.id,
            product_name=product.name,
            price=product.price,
            quantity=item_data.quantity,  # Agora funciona!
            observation=item_data.observation  # Agora funciona!
        )
        db.add(db_item)

    db.commit()

    return {"message": "Pedido realizado com sucesso!", "order_id": new_order.id}


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

    order.status = status_data.status
    db.commit()

    return {"message": "Status atualizado com sucesso", "status": order.status}


# Rota para o CLIENTE buscar seus pedidos pelo nome
@router.get("/orders/customer/{customer_name}", response_model=List[OrderResponse])
def get_customer_orders(customer_name: str, db: Session = Depends(get_db)):
    print(f"👤 Buscando histórico de: {customer_name}")
    orders = db.query(OrderDB).filter(
        OrderDB.customer_name == customer_name
    ).order_by(OrderDB.id.desc()).all()
    return orders