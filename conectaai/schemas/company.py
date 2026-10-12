# Arquivo: conectaai/schemas/company.py
from typing import List, Optional

from pydantic import BaseModel

from conectaai.schemas.creator import PortfolioItem


class CompanyUpdateRequest(BaseModel):
    name: Optional[str] = None
    segment: Optional[str] = None
    city: Optional[str] = None
    website: Optional[str] = None
    instagram: Optional[str] = None
    size: Optional[str] = None
    avg_campaign_budget: Optional[float] = None
    desired_categories: Optional[List[str]] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    portfolio: Optional[List[PortfolioItem]] = None


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
    bio: str = ""
    avatar_url: str = ""
    portfolio: List[PortfolioItem] = []

    class Config:
        from_attributes = True


def company_to_response(company) -> "CompanyResponse":
    """Construído à mão (em vez de `model_validate`/`from_attributes` direto)
    porque linhas de empresa criadas antes das colunas `bio`/`avatar_url`/
    `portfolio` existirem podem trazer `NULL` do banco — os `or` abaixo
    evitam que isso quebre a validação do Pydantic (que não aceita `None`
    nesses campos, só `str`/`list`)."""
    return CompanyResponse(
        id=company.id,
        name=company.name,
        segment=company.segment or "",
        city=company.city or "",
        website=company.website or "",
        instagram=company.instagram or "",
        size=company.size or "",
        avg_campaign_budget=company.avg_campaign_budget or 0,
        desired_categories=company.desired_categories or [],
        bio=company.bio or "",
        avatar_url=company.avatar_url or "",
        portfolio=company.portfolio or [],
    )
