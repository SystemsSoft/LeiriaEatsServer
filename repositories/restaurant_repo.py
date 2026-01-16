# Arquivo: repositories/restaurant_repo.py
from sqlalchemy.orm import Session, joinedload
from typing import List
from core.sql_models import RestaurantDB, ProductDB  # Certifique-se de importar ProductDB se for usar
from schemas.company import CompanyCreateRequest
from schemas.product import ProductCreateRequest

# --- CORREÇÃO: Importações necessárias para a criptografia ---
from passlib.context import CryptContext

# --- CORREÇÃO: Inicialização do contexto de criptografia ---
# Isso cria o objeto que sabe como fazer o hash (embaralhar) a senha
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class RestaurantRepository:

    @staticmethod
    def get_all(db: Session) -> List[RestaurantDB]:
        return db.query(RestaurantDB).options(joinedload(RestaurantDB.products)).all()

    @staticmethod
    def get_by_id(db: Session, restaurant_id: int) -> RestaurantDB:
        return db.query(RestaurantDB).filter(RestaurantDB.id == restaurant_id).first()

    @staticmethod
    def create_company(db: Session, company: CompanyCreateRequest):
        # 1. Criptografa a senha antes de salvar
        # Agora o 'pwd_context' existe e vai funcionar
        hashed_password = pwd_context.hash(company.password)

        # 2. Cria o objeto do banco com a senha criptografada
        db_restaurant = RestaurantDB(
            name=company.name,
            category=company.category,
            phone=company.phone,
            address=company.address,
            image_url=company.image_url,

            # Novos campos
            login=company.login,
            password=hashed_password,  # SALVA O HASH!
            license=company.license
        )

        db.add(db_restaurant)
        db.commit()
        db.refresh(db_restaurant)
        return db_restaurant

    # Adicionei o método create_product aqui também para garantir que não falte
    @staticmethod
    def create_product(db: Session, product: ProductCreateRequest):
        # Verifica se product.restaurant_id é válido antes de criar (opcional, mas recomendado)

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