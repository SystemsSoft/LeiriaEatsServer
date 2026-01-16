from sqlalchemy.orm import Session, joinedload
from typing import List
from core.sql_models import RestaurantDB
# ATENÇÃO: Importe os novos schemas aqui
from schemas.company import CompanyCreateRequest, CompanyResponse

class RestaurantRepository:
    @staticmethod
    def get_all(db: Session) -> List[RestaurantDB]:
        return db.query(RestaurantDB).options(joinedload(RestaurantDB.products)).all()

    @staticmethod
    def get_by_id(db: Session, restaurant_id: int) -> RestaurantDB:
        return db.query(RestaurantDB).filter(RestaurantDB.id == restaurant_id).first()


    @staticmethod
    def create_company(db: Session, company_data: CompanyCreateRequest) -> RestaurantDB:
        """
        Cria um novo registro na tabela restaurants usando os dados
        vindos do formulário da empresa (nome, telefone, endereço).
        """
        db_company = RestaurantDB(
            name=company_data.name,
            phone=company_data.phone,       # Mapeando os campos novos
            address=company_data.address,   # Mapeando os campos novos
            # Os campos abaixo pegam os valores padrão definidos no schema se não vierem
            category=company_data.category,
            image_url=company_data.image_url,
            rating=company_data.rating
        )

        db.add(db_company)
        db.commit()
        db.refresh(db_company) # Recarrega o objeto com o ID gerado pelo banco
        return db_company