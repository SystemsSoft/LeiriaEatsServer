from sqlalchemy.orm import Session, joinedload
from typing import List
from core.sql_models import RestaurantDB, ProductDB
from schemas.company import CompanyCreateRequest
from schemas.product import ProductCreateRequest

class RestaurantRepository:

    @staticmethod
    def get_all(db: Session) -> List[RestaurantDB]:
        return db.query(RestaurantDB).options(joinedload(RestaurantDB.products)).all()

    @staticmethod
    def get_by_id(db: Session, restaurant_id: int) -> RestaurantDB:
        return db.query(RestaurantDB).filter(RestaurantDB.id == restaurant_id).first()

    @staticmethod
    def create_company(db: Session, company: CompanyCreateRequest):

        db_restaurant = RestaurantDB(
            name=company.name,
            category=company.category,
            phone=company.phone,
            address=company.address,
            image_url=company.image_url,

            # Novos campos
            login=company.login,
            password=company.password,
            license=company.license,
            plan=company.plan
        )

        db.add(db_restaurant)
        db.commit()
        db.refresh(db_restaurant)
        return db_restaurant

    @staticmethod
    def create_product(db: Session, product: ProductCreateRequest):
        db_product = ProductDB(
            name=product.name,
            description=product.description,
            price=product.price,
            image_url=product.image_url,
            restaurant_id=product.restaurant_id
        )
        db.add(db_product)
        db.commit()
        db.refresh(db_product)
        return db_product

    @staticmethod
    def check_credentials(db: Session, login: str, plain_password: str):
        # 1. Busca o usuário pelo login
        user = db.query(RestaurantDB).filter(RestaurantDB.login == login).first()

        if not user:
            return None

        if plain_password != user.password:
            return None

        return user