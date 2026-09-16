# Arquivo: conectaai/repositories/company_repo.py
from typing import Optional

from sqlalchemy.orm import Session

from conectaai.models.sql_models import CompanyDB


class CompanyRepository:
    @staticmethod
    def get_by_user_id(db: Session, user_id: str) -> Optional[CompanyDB]:
        return db.query(CompanyDB).filter(CompanyDB.user_id == user_id).first()

    @staticmethod
    def get_by_id(db: Session, company_id: str) -> Optional[CompanyDB]:
        return db.query(CompanyDB).filter(CompanyDB.id == company_id).first()

    @staticmethod
    def update(db: Session, company: CompanyDB, data: dict) -> CompanyDB:
        for key, value in data.items():
            setattr(company, key, value)
        db.commit()
        db.refresh(company)
        return company
