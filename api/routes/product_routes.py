# Arquivo: api/routes/product_routes.py
from typing import List
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from core.database import get_db
# 1. IMPORTAMOS O MODELO DE BANCO CORRETO
from core.sql_models import ProductDB, RestaurantDB
# 2. IMPORTAMOS O SCHEMA DE DADOS
from schemas.product import ProductCreateRequest, ProductResponse

router = APIRouter()


# --- CRIAR ---
@router.post("/product", response_model=ProductResponse, status_code=201)
def create_product(product_data: ProductCreateRequest, db: Session = Depends(get_db)):
    print(f"🍔 Criando produto: {product_data.name}")

    # Verifica se o restaurante existe usando RestaurantDB
    restaurant = db.query(RestaurantDB).filter(RestaurantDB.id == product_data.restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurante não encontrado.")

    # Cria o objeto ProductDB (Banco de Dados)
    new_product = ProductDB(
        name=product_data.name,
        description=product_data.description,
        price=product_data.price,
        image_url=product_data.image_url,
        restaurant_id=product_data.restaurant_id,
        category=product_data.category,
       preparation_time = product_data.preparation_time
    )

    db.add(new_product)
    db.commit()
    db.refresh(new_product)

    return new_product


# --- LISTAR (Onde estava o erro) ---
@router.get("/products/restaurant/{restaurant_id}", response_model=List[ProductResponse])
def get_products_by_restaurant(restaurant_id: int, db: Session = Depends(get_db)):
    # 3. USAMOS EXPLICITAMENTE ProductDB AQUI
    products = db.query(ProductDB).filter(ProductDB.restaurant_id == restaurant_id).all()
    return products


# --- ATUALIZAR ---
@router.put("/product/{product_id}", response_model=ProductResponse)
def update_product(product_id: int, product_data: ProductCreateRequest, db: Session = Depends(get_db)):
    db_product = db.query(ProductDB).filter(ProductDB.id == product_id).first()

    if not db_product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    db_product.name = product_data.name
    db_product.description = product_data.description
    db_product.price = product_data.price
    db_product.category = product_data.category
    db_product.preparation_time = product_data.preparation_time

    if product_data.image_url:
        db_product.image_url = product_data.image_url

    db.commit()
    db.refresh(db_product)
    return db_product


# --- DELETAR ---
@router.delete("/product/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    db_product = db.query(ProductDB).filter(ProductDB.id == product_id).first()

    if not db_product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    db.delete(db_product)
    db.commit()
    return {"message": "Deletado com sucesso"}