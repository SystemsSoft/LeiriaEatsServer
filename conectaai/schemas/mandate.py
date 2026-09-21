# Arquivo: conectaai/schemas/mandate.py
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DeliverableSpec(BaseModel):
    content_type: str
    min_qty: int = 1
    max_qty: int = 1


class MandateCreateRequest(BaseModel):
    campaign_id: Optional[str] = None
    objective: str = ""
    target_count: int = 0
    budget_total: float = Field(default=0, ge=0)
    ideal_price: float = Field(default=0, ge=0)
    price_floor: float = Field(default=0, ge=0)
    price_ceiling: float = Field(default=0, ge=0)
    auto_approve_limit: float = Field(default=0, ge=0)
    currency: str = "BRL"
    max_rounds: int = Field(default=4, ge=1, le=10)
    deliverables: List[DeliverableSpec] = Field(default_factory=list)
    negotiable_fields: List[str] = Field(default_factory=list)
    non_negotiable_fields: List[str] = Field(default_factory=list)
    deadline_earliest: Optional[datetime] = None
    deadline_latest: Optional[datetime] = None
    exclusivity_allowed: bool = False
    extra_terms: Dict[str, Any] = Field(default_factory=dict)
    expires_at: Optional[datetime] = None


class MandateUpdateRequest(MandateCreateRequest):
    active: Optional[bool] = None


class MandateResponse(BaseModel):
    id: str
    owner_type: str
    owner_id: str
    campaign_id: Optional[str]
    active: bool
    objective: str
    target_count: int
    budget_total: float
    ideal_price: float
    price_floor: float
    price_ceiling: float
    auto_approve_limit: float
    currency: str
    max_rounds: int
    deliverables: List[dict]
    negotiable_fields: List[str]
    non_negotiable_fields: List[str]
    deadline_earliest: Optional[datetime]
    deadline_latest: Optional[datetime]
    exclusivity_allowed: bool
    extra_terms: Dict[str, Any]
    expires_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
