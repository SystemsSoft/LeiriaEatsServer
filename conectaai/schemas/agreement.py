# Arquivo: conectaai/schemas/agreement.py
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel


class AgreementResponse(BaseModel):
    id: str
    negotiation_id: str
    company_id: str
    creator_id: str
    campaign_id: Optional[str]
    terms: Dict[str, Any]
    total_value: float
    status: str
    company_approved_at: Optional[datetime]
    creator_approved_at: Optional[datetime]
    rejected_by: str
    reject_reason: str
    proposal_id: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class RejectAgreementRequest(BaseModel):
    reason: str = ""
