# Arquivo: conectaai/schemas/proposal.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


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


class CreatorProposalCreateRequest(BaseModel):
    """Candidatura do creator a uma campanha: a empresa é a dona da campanha, não vem no corpo."""

    campaign_id: str
    content_type: str = ""
    quantity: int = Field(default=1, ge=1, le=100)
    deadline: Optional[datetime] = None
    description: str = ""
    budget: float = Field(default=0, ge=0, le=1_000_000)
    message: str = Field(default="", max_length=1000)


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
    sender_role: str = "company"

    class Config:
        from_attributes = True
