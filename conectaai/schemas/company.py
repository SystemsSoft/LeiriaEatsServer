# Arquivo: conectaai/schemas/company.py
from typing import List, Optional

from pydantic import BaseModel


class CompanyUpdateRequest(BaseModel):
    name: Optional[str] = None
    segment: Optional[str] = None
    city: Optional[str] = None
    website: Optional[str] = None
    instagram: Optional[str] = None
    size: Optional[str] = None
    avg_campaign_budget: Optional[float] = None
    desired_categories: Optional[List[str]] = None


class CompanyResponse(BaseModel):
    id: str
    name: str
    segment: str
    city: str
    website: str
    instagram: str
    size: str
    avg_campaign_budget: float
    desired_categories: List[str]

    class Config:
        from_attributes = True
