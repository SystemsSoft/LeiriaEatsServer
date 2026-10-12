# Arquivo: conectaai/repositories/favorite_repo.py
from typing import List, Optional

from sqlalchemy.orm import Session

from conectaai.models.sql_models import FavoriteDB


class FavoriteRepository:
    @staticmethod
    def get_all_for_user(db: Session, user_id: str) -> List[FavoriteDB]:
        return db.query(FavoriteDB).filter(FavoriteDB.user_id == user_id).all()

    @staticmethod
    def find(db: Session, user_id: str, target_type: str, target_id: str) -> Optional[FavoriteDB]:
        return (
            db.query(FavoriteDB)
            .filter(
                FavoriteDB.user_id == user_id,
                FavoriteDB.target_type == target_type,
                FavoriteDB.target_id == target_id,
            )
            .first()
        )

    @staticmethod
    def create(db: Session, user_id: str, target_type: str, target_id: str) -> FavoriteDB:
        favorite = FavoriteDB(user_id=user_id, target_type=target_type, target_id=target_id)
        db.add(favorite)
        db.commit()
        db.refresh(favorite)
        return favorite

    @staticmethod
    def delete(db: Session, favorite: FavoriteDB) -> None:
        db.delete(favorite)
        db.commit()
