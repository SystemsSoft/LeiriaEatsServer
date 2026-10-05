# Arquivo: conectaai/schemas/collaboration.py
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class DeliverableEventResponse(BaseModel):
    kind: str  # submitted | approved | changes_requested
    actor_role: str  # creator | company
    url: str
    message: str
    created_at: datetime

    class Config:
        from_attributes = True


class DeliverableResponse(BaseModel):
    id: str
    position: int
    label: str
    content_type: str
    status: str  # pending | submitted | changes_requested | approved
    url: str
    note: str
    feedback: str
    submitted_at: Optional[datetime]
    reviewed_at: Optional[datetime]
    events: List[DeliverableEventResponse] = []

    class Config:
        from_attributes = True


class CollaborationCounts(BaseModel):
    total: int = 0
    pending: int = 0
    submitted: int = 0
    changes_requested: int = 0
    approved: int = 0


class CollaborationSummary(BaseModel):
    """Um acordo (proposta aceita) com o andamento dos conteúdos."""

    proposal_id: str
    campaign_id: Optional[str]
    campaign_name: str
    company_id: str
    company_name: str
    company_avatar_url: str = ""
    creator_id: str
    creator_name: str
    creator_avatar_url: str = ""
    content_type: str
    quantity: int
    price: float
    deadline: Optional[datetime]
    counts: CollaborationCounts
    status: str  # in_progress | completed
    last_activity_at: datetime


class CollaborationDetail(CollaborationSummary):
    deliverables: List[DeliverableResponse] = []


class SubmitDeliverableRequest(BaseModel):
    url: str = Field(min_length=1, max_length=1000)
    note: str = Field(default="", max_length=1000)


class ReviewDeliverableRequest(BaseModel):
    decision: str  # "approve" | "request_changes"
    feedback: str = Field(default="", max_length=1000)
