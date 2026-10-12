# Arquivo: conectaai/repositories/agreement_repo.py
from typing import List, Optional

from sqlalchemy.orm import Session

from conectaai.models.sql_models import AgreementDB


class AgreementRepository:
    @staticmethod
    def get_by_id(db: Session, agreement_id: str) -> Optional[AgreementDB]:
        return db.query(AgreementDB).filter(AgreementDB.id == agreement_id).first()

    @staticmethod
    def get_by_negotiation_id(db: Session, negotiation_id: str) -> Optional[AgreementDB]:
        return db.query(AgreementDB).filter(AgreementDB.negotiation_id == negotiation_id).first()

    @staticmethod
    def get_all_for_user(db: Session, *, company_id: Optional[str], creator_id: Optional[str]) -> List[AgreementDB]:
        query = db.query(AgreementDB)
        if company_id:
            query = query.filter(AgreementDB.company_id == company_id)
        if creator_id:
            query = query.filter(AgreementDB.creator_id == creator_id)
        return query.order_by(AgreementDB.created_at.desc()).all()

    @staticmethod
    def create(db: Session, data: dict) -> AgreementDB:
        agreement = AgreementDB(**data)
        db.add(agreement)
        db.commit()
        db.refresh(agreement)
        return agreement

    @staticmethod
    def update(db: Session, agreement: AgreementDB, data: dict) -> AgreementDB:
        for key, value in data.items():
            setattr(agreement, key, value)
        db.commit()
        db.refresh(agreement)
        return agreement
