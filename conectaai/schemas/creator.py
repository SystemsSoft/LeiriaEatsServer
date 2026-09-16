# Arquivo: conectaai/schemas/creator.py
from typing import Dict, List, Optional

from pydantic import BaseModel


class AudienceInfo(BaseModel):
    female_percent: int = 0
    male_percent: int = 0
    age_ranges: Dict[str, int] = {}
    top_locations: Dict[str, int] = {}
    interests: List[str] = []


class PortfolioItem(BaseModel):
    title: str
    type: str
    metric: str


class TimeSeriesPoint(BaseModel):
    label: str
    value: float


class MatchBreakdown(BaseModel):
    niche: int = 0
    audience: int = 0
    location: int = 0
    engagement: int = 0
    price: int = 0


class CreatorUpdateRequest(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None
    city: Optional[str] = None
    bio: Optional[str] = None
    categories: Optional[List[str]] = None
    followers: Optional[int] = None
    engagement_rate: Optional[float] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    platforms: Optional[List[str]] = None
    available: Optional[bool] = None
    audience: Optional[AudienceInfo] = None
    portfolio: Optional[List[PortfolioItem]] = None


class CreatorResponse(BaseModel):
    id: str
    name: str
    username: str
    city: str
    bio: str
    categories: List[str]
    followers: int
    engagement_rate: float
    audience: AudienceInfo
    price_min: float
    price_max: float
    platforms: List[str]
    rating: float
    available: bool
    portfolio: List[PortfolioItem]
    followers_history: List[TimeSeriesPoint]
    engagement_history: List[TimeSeriesPoint]

    # Calculados em tempo de request pelo ai_service quando há contexto de busca
    # (não são colunas persistidas) — ausentes fora do fluxo de chat/matching.
    match_score: Optional[int] = None
    match_breakdown: Optional[MatchBreakdown] = None
    match_reason: Optional[str] = None

    class Config:
        from_attributes = True


def creator_to_response(creator) -> "CreatorResponse":
    """Constrói o schema a partir do model SQLAlchemy — feito à mão (em vez de
    `model_validate`/`from_attributes`) porque o nome da coluna no banco
    (`audience_info`) não bate com o nome do campo na API (`audience`)."""
    return CreatorResponse(
        id=creator.id,
        name=creator.name,
        username=creator.username,
        city=creator.city,
        bio=creator.bio,
        categories=creator.categories or [],
        followers=creator.followers,
        engagement_rate=creator.engagement_rate,
        audience=creator.audience_info or {},
        price_min=creator.price_min,
        price_max=creator.price_max,
        platforms=creator.platforms or [],
        rating=creator.rating,
        available=creator.available,
        portfolio=creator.portfolio or [],
        followers_history=creator.followers_history or [],
        engagement_history=creator.engagement_history or [],
    )
