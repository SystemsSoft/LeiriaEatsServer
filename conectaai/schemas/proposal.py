# Arquivo: conectaai/schemas/proposal.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ProposalCreateRequest(BaseModel):
    creator_id: str
    campaign_id: Optional[str] = None
    campaign_name: str = ""
    content_type: str = ""
    quantity: int = 1
    deadline: Optional[datetime] = None
    description: str = ""
    budget: float = 0
    message: str = ""


class ProposalUpdateRequest(BaseModel):
    status: Optional[str] = None
    message: Optional[str] = None


class ProposalResponse(BaseModel):
    id: str
    company_id: str
    creator_id: str
    campaign_id: Optional[str]
    campaign_name: str
    content_type: str
    quantity: int
    deadline: Optional[datetime]
    description: str
    budget: float
    status: str
    message: str

    class Config:
        from_attributes = True
