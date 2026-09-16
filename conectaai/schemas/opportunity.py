# Arquivo: conectaai/schemas/opportunity.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class OpportunityResponse(BaseModel):
    id: str
    company_name: str
    campaign_name: str
    content_type: str
    category: str
    budget_min: float
    budget_max: float
    deadline: Optional[datetime]

    # Calculado em tempo de request pelo ai_service quando há contexto de busca.
    match_score: Optional[int] = None

    class Config:
        from_attributes = True
