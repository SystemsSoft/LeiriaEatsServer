# Arquivo: conectaai/schemas/campaign.py
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class CampaignCreateRequest(BaseModel):
    name: str
    creator_ids: List[str] = []
    budget: float = 0
    status: str = "draft"
    deadline: Optional[datetime] = None
    description: str = ""
    content_types: List[str] = []


class CampaignUpdateRequest(BaseModel):
    name: Optional[str] = None
    creator_ids: Optional[List[str]] = None
    budget: Optional[float] = None
    status: Optional[str] = None
    deadline: Optional[datetime] = None
    description: Optional[str] = None
    progress: Optional[float] = None
    current_stage: Optional[str] = None
    content_types: Optional[List[str]] = None


class CampaignResponse(BaseModel):
    id: str
    name: str
    company_id: str
    creator_ids: List[str]
    budget: float
    status: str
    deadline: Optional[datetime]
    description: str
    progress: float
    current_stage: str
    content_types: List[str]

    class Config:
        from_attributes = True
