# Arquivo: conectaai/schemas/favorite.py
from datetime import datetime

from pydantic import BaseModel


class FavoriteCreateRequest(BaseModel):
    target_type: str  # "creator" | "opportunity"
    target_id: str


class FavoriteResponse(BaseModel):
    id: str
    target_type: str
    target_id: str
    created_at: datetime

    class Config:
        from_attributes = True
