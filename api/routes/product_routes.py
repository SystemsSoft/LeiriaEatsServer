from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from core.database import get_db
from repositories.restaurant_repo import RestaurantRepository
from schemas.product import ProductCreateRequest, ProductResponse

router = APIRouter()


@router.post("/product", response_model=ProductResponse, status_code=201)
def create_product(
        product_data: ProductCreateRequest,
        db: Session = Depends(get_db)
):
    print(f"🍔 Recebendo cadastro de produto: {product_data.name}")

    try:
        restaurant = RestaurantRepository.get_by_id(db, product_data.restaurant_id)
        if not restaurant:
            raise HTTPException(status_code=404, detail="Restaurante não encontrado.")

        new_product = RestaurantRepository.create_product(db, product_data)
        print(f"✅ Produto criado com ID: {new_product.id}")
        return new_product
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"❌ Erro ao criar produto: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao salvar produto.")