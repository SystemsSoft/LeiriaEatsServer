# Arquivo: conectaai/schemas/rating.py
from typing import Optional

from pydantic import BaseModel, Field


class RatingRequest(BaseModel):
    score: int = Field(ge=1, le=5)


class RatingResponse(BaseModel):
    score: Optional[int] = None
