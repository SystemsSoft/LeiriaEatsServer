# Arquivo: conectaai/repositories/notification_repo.py
from typing import List

from sqlalchemy.orm import Session

from conectaai.models.sql_models import NotificationDB


class NotificationRepository:
    @staticmethod
    def get_all_for_user(db: Session, user_id: str) -> List[NotificationDB]:
        return (
            db.query(NotificationDB)
            .filter(NotificationDB.user_id == user_id)
            .order_by(NotificationDB.timestamp.desc())
            .all()
        )

    @staticmethod
    def mark_all_as_read(db: Session, user_id: str) -> None:
        db.query(NotificationDB).filter(NotificationDB.user_id == user_id).update({"read": True})
        db.commit()

    @staticmethod
    def create(db: Session, *, user_id: str, type_: str, title: str, message: str) -> NotificationDB:
        notification = NotificationDB(user_id=user_id, type=type_, title=title, message=message)
        db.add(notification)
        db.commit()
        db.refresh(notification)
        return notification
