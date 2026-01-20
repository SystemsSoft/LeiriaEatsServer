# Arquivo: api/routes/order_routes.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from core.database import get_db
from core.sql_models import OrderDB, OrderItemDB, ProductDB
from schemas.models import OrderCreate

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