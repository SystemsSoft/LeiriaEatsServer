# Arquivo: conectaai/schemas/notification.py
from datetime import datetime

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: str
    type: str
    title: str
    message: str
    timestamp: datetime
    read: bool

    class Config:
        from_attributes = True
