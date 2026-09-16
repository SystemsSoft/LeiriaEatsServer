# Arquivo: conectaai/api/routes/notification_routes.py
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, get_current_user
from conectaai.repositories.notification_repo import NotificationRepository
from conectaai.schemas.notification import NotificationResponse

router = APIRouter(prefix="/notifications", tags=["Notificações"])


@router.get("", response_model=List[NotificationResponse])
def list_notifications(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return NotificationRepository.get_all_for_user(db, current_user.user_id)


@router.post("/mark-all-read", status_code=204)
def mark_all_as_read(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    NotificationRepository.mark_all_as_read(db, current_user.user_id)
