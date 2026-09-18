# Arquivo: conectaai/repositories/rating_repo.py
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from conectaai.models.sql_models import RatingDB


class RatingRepository:
    @staticmethod
    def get_by_company_and_creator(db: Session, company_id: str, creator_id: str) -> Optional[RatingDB]:
        return (
            db.query(RatingDB)
            .filter(RatingDB.company_id == company_id, RatingDB.creator_id == creator_id)
            .first()
        )

    @staticmethod
    def upsert(db: Session, company_id: str, creator_id: str, score: int) -> RatingDB:
        rating = RatingRepository.get_by_company_and_creator(db, company_id, creator_id)
        if rating:
            rating.score = score
        else:
            rating = RatingDB(company_id=company_id, creator_id=creator_id, score=score)
            db.add(rating)
        db.commit()
        db.refresh(rating)
        return rating

    @staticmethod
    def average_for_creator(db: Session, creator_id: str) -> float:
        result = db.query(func.avg(RatingDB.score)).filter(RatingDB.creator_id == creator_id).scalar()
        return round(float(result), 1) if result is not None else 0.0
