# Arquivo: api/routes/product_routes.py
from typing import List
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func

from core.database import get_db
# 1. IMPORTAMOS O MODELO DE BANCO CORRETO
from core.sql_models import ProductDB, RestaurantDB, ProductRatingDB
# 2. IMPORTAMOS O SCHEMA DE DADOS
from schemas.product import ProductCreateRequest, ProductResponse
from services.ai_service import AIService
from ulid import ULID

router = APIRouter()


# --- CRIAR ---
@router.post("/product", response_model=ProductResponse, status_code=201)
def create_product(product_data: ProductCreateRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    print(f"🍔 Criando produto: {product_data.name}")

    # Pega o GID de qualquer um dos campos (preferência para restaurant_gid)
    res_gid = product_data.restaurant_gid or product_data.restaurant_id
    if not res_gid:
        raise HTTPException(status_code=400, detail="restaurant_gid é obrigatório.")

    # Verifica se o restaurante existe usando RestaurantDB
    restaurant = db.query(RestaurantDB).filter(RestaurantDB.gid == res_gid).first()
    
    # Fallback para ID numérico se não for GID
    if not restaurant and str(res_gid).isdigit():
        restaurant = db.query(RestaurantDB).filter(RestaurantDB.id == int(res_gid)).first()

    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurante não encontrado.")

    # Cria o objeto ProductDB (Banco de Dados)
    new_product = ProductDB(
        gid=str(ULID()), 
        name=product_data.name,
        description=product_data.description,
        price=product_data.price,
        image_url=product_data.image_url,
        restaurant_id=restaurant.id,
        category=product_data.category,
        preparation_time=product_data.preparation_time,
        
        # Colunas de IA
        ingredients=product_data.ingredients,
        allergens=product_data.allergens,
        dietary_tags=product_data.dietary_tags,
        spice_level=product_data.spice_level,
        serves_people=product_data.serves_people,
        portion_size=product_data.portion_size,
        calories=product_data.calories,
        is_popular=product_data.is_popular,
        is_available=product_data.is_available,
        is_surprise_box=product_data.is_surprise_box,
        preparation_time_minutes=product_data.preparation_time_minutes,
        recommended_for=product_data.recommended_for,
        search_tags=product_data.search_tags
    )

    try:
        db.add(new_product)
        db.commit()
        db.refresh(new_product)

        # Reindexação incremental em background (Plano de Performance, Fase 3.1/3.2) —
        # não bloqueia a resposta HTTP nem disputa CPU com chats em andamento.
        background_tasks.add_task(AIService.reindex_single_product, new_product.id, db)
        print(f"🔄 Reindexação do novo produto agendada em background")
    except Exception as e:
        db.rollback()
        print(f"❌ Erro ao criar produto: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao criar produto: {str(e)}")

    return new_product


# --- LISTAR (Onde estava o erro) ---
@router.get("/products/restaurant/{gid}", response_model=List[ProductResponse])
def get_products_by_restaurant(gid: str, db: Session = Depends(get_db)):
    try:
        print(f"🔎 Buscando produtos para restaurante GID: {gid}")
        restaurant = db.query(RestaurantDB).filter(RestaurantDB.gid == gid).first()
        if not restaurant:
            raise HTTPException(status_code=404, detail="Restaurante não encontrado")

        from sqlalchemy.orm import joinedload
        # Usamos joinedload para carregar o restaurante e permitir o acesso à propriedade restaurant_gid
        products = db.query(ProductDB).options(joinedload(ProductDB.restaurant)).filter(
            ProductDB.restaurant_id == restaurant.id
        ).all()

        # Calcula a média de rating para cada produto, filtrado pelo restaurante
        avg_ratings = dict(
            db.query(
                ProductRatingDB.product_id,
                func.avg(ProductRatingDB.rating)
            )
            .filter(ProductRatingDB.restaurant_id == restaurant.id)
            .group_by(ProductRatingDB.product_id)
            .all()
        )

        for product in products:
            # Atribui o rating calculado (não persistido) para a resposta
            product.rating = avg_ratings.get(product.id)

        return products
    except Exception as e:
        print(f"❌ Erro em get_products_by_restaurant: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# --- ATUALIZAR ---
@router.put("/product/{gid}", response_model=ProductResponse)
def update_product(gid: str, product_data: ProductCreateRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    db_product = db.query(ProductDB).filter(ProductDB.gid == gid).first()

    if not db_product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    db_product.name = product_data.name
    db_product.description = product_data.description
    db_product.price = product_data.price
    db_product.category = product_data.category
    db_product.preparation_time = product_data.preparation_time

    # Atualizar Colunas de IA se presentes
    db_product.ingredients = product_data.ingredients
    db_product.allergens = product_data.allergens
    db_product.dietary_tags = product_data.dietary_tags
    db_product.spice_level = product_data.spice_level
    db_product.serves_people = product_data.serves_people
    db_product.portion_size = product_data.portion_size
    db_product.calories = product_data.calories
    db_product.is_popular = product_data.is_popular
    db_product.is_available = product_data.is_available
    db_product.is_surprise_box = product_data.is_surprise_box
    db_product.preparation_time_minutes = product_data.preparation_time_minutes
    db_product.recommended_for = product_data.recommended_for
    db_product.search_tags = product_data.search_tags

    if product_data.image_url:
        db_product.image_url = product_data.image_url

    db.commit()
    db.refresh(db_product)

    # Calcula a média de rating filtrada por restaurante
    avg = db.query(func.avg(ProductRatingDB.rating)).filter(
        ProductRatingDB.product_id == db_product.id,
        ProductRatingDB.restaurant_id == db_product.restaurant_id
    ).scalar()
    db_product.rating = avg

    # Reindexação incremental em background — ver nota em create_product.
    background_tasks.add_task(AIService.reindex_single_product, db_product.id, db)
    print(f"🔄 Reindexação do produto atualizado agendada em background")

    return db_product


# --- DELETAR ---
@router.delete("/product/{gid}")
def delete_product(gid: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    db_product = db.query(ProductDB).filter(ProductDB.gid == gid).first()

    if not db_product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    # Captura o id ANTES do delete+commit — depois do commit o objeto fica expirado e
    # acessar db_product.id disparava um novo SELECT por um produto que já não existe.
    deleted_product_id = db_product.id

    db.delete(db_product)
    db.commit()

    # Reindexação incremental em background — ver nota em create_product. Como o
    # produto já foi deletado, reindex_single_product vai apenas removê-lo do índice.
    background_tasks.add_task(AIService.reindex_single_product, deleted_product_id, db)
    print(f"🔄 Remoção do produto do índice agendada em background")

    return {"message": "Deletado com sucesso"}