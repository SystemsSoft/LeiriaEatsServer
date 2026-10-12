# Arquivo: conectaai/repositories/creator_repo.py
from typing import List, Optional

from sqlalchemy.orm import Session

from conectaai.models.sql_models import CreatorDB


class CreatorRepository:
    @staticmethod
    def get_all(db: Session) -> List[CreatorDB]:
        return db.query(CreatorDB).all()

    @staticmethod
    def get_by_id(db: Session, creator_id: str) -> Optional[CreatorDB]:
        return db.query(CreatorDB).filter(CreatorDB.id == creator_id).first()

    @staticmethod
    def get_by_user_id(db: Session, user_id: str) -> Optional[CreatorDB]:
        return db.query(CreatorDB).filter(CreatorDB.user_id == user_id).first()

    @staticmethod
    def get_many_by_ids(db: Session, creator_ids: List[str]) -> List[CreatorDB]:
        if not creator_ids:
            return []
        return db.query(CreatorDB).filter(CreatorDB.id.in_(creator_ids)).all()

    @staticmethod
    def update(db: Session, creator: CreatorDB, data: dict) -> CreatorDB:
        for key, value in data.items():
            setattr(creator, key, value)
        db.commit()
        db.refresh(creator)
        return creator
