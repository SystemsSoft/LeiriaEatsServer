# Arquivo: conectaai/repositories/opportunity_repo.py
from typing import List

from sqlalchemy.orm import Session

from conectaai.models.sql_models import OpportunityDB


class OpportunityRepository:
    @staticmethod
    def get_all(db: Session) -> List[OpportunityDB]:
        return db.query(OpportunityDB).all()

    @staticmethod
    def get_many_by_ids(db: Session, ids: List[str]) -> List[OpportunityDB]:
        if not ids:
            return []
        return db.query(OpportunityDB).filter(OpportunityDB.id.in_(ids)).all()
