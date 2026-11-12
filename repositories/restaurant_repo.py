from sqlalchemy.orm import Session, joinedload
from core.senhas import gerar_hash, precisa_rehash, verificar
from typing import List
from core.sql_models import RestaurantDB, ProductDB
from schemas.company import CompanyCreateRequest
from schemas.product import ProductCreateRequest
from ulid import ULID

class RestaurantRepository:

    @staticmethod
    def get_all(db: Session) -> List[RestaurantDB]:
        return db.query(RestaurantDB).options(
            joinedload(RestaurantDB.products).joinedload(ProductDB.restaurant)
        ).all()

    @staticmethod
    def get_by_id(db: Session, restaurant_id: int) -> RestaurantDB:
        return db.query(RestaurantDB).filter(RestaurantDB.id == restaurant_id).first()

    @staticmethod
    def get_by_gid(db: Session, gid: str) -> RestaurantDB:
        return db.query(RestaurantDB).filter(RestaurantDB.gid == gid).first()

    @staticmethod
    def create_company(db: Session, company: CompanyCreateRequest):

        db_restaurant = RestaurantDB(
            name=company.name,
            category=company.category,
            phone=company.phone,
            address=company.address,
            image_url=company.image_url,
            latitude=company.latitude,
            longitude=company.longitude,

            # Novos campos
            login=company.login,
            password=gerar_hash(company.password),
            license=company.license,
            plan=company.plan,
            gid=str(ULID()), # Gera um novo ULID para o restaurante
            status="PENDING",  # Status inicial
            has_surprise_box=company.has_surprise_box,
            surprise_box_qty=company.surprise_box_qty,
            surprise_box_pickup_start=company.surprise_box_pickup_start,
            surprise_box_pickup_end=company.surprise_box_pickup_end,
        )

        db.add(db_restaurant)
        db.commit()
        db.refresh(db_restaurant)
        return db_restaurant

    @staticmethod
    def create_product(db: Session, product: ProductCreateRequest):
        db_product = ProductDB(
            gid=str(ULID()), # Gera um novo ULID para o produto
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

        # Senhas em PBKDF2 (core/senhas.py). Contas registadas antes de 12/11/2026 guardavam a
        # senha em texto simples: ainda entram e ficam convertidas aqui, no primeiro login.
        if not verificar(plain_password, user.password, aceitar_texto_simples=True):
            return None
        if precisa_rehash(user.password):
            user.password = gerar_hash(plain_password)
            db.commit()
        return user