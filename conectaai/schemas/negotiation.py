# Arquivo: conectaai/schemas/negotiation.py
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class StartNegotiationRequest(BaseModel):
    creator_id: str
    campaign_id: Optional[str] = None
    company_mandate_id: str


class StartNegotiationAsCreatorRequest(BaseModel):
    """O creator inicia a negociação a partir de uma oportunidade encontrada
    na busca semântica (`/ai/match/opportunities`) — informa só o mandato da
    empresa que encontrou; o resto (company_id, campaign_id) é derivado dele,
    e o mandato do próprio creator é o dele já ativo (precisa existir)."""

    company_mandate_id: str


class NegotiationTurnResponse(BaseModel):
    id: str
    round_no: int
    actor: str
    intent: str
    terms_after_policy: Dict[str, Any]
    policy_violations: List[str]
    message_text: str
    created_at: datetime

    class Config:
        from_attributes = True


class NegotiationResponse(BaseModel):
    id: str
    company_id: str
    creator_id: str
    campaign_id: Optional[str]
    conversation_id: Optional[str]
    company_mandate_id: str
    creator_mandate_id: Optional[str]
    state: str
    round_no: int
    max_rounds: int
    current_offer: Dict[str, Any]
    last_actor: str
    outcome: str
    outcome_reason: str
    agreement_id: Optional[str] = None
    turns: List[NegotiationTurnResponse] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class RaiseAutoLimitRequest(BaseModel):
    new_auto_limit: float


class AiCallSummary(BaseModel):
    model: str
    key_index: int
    status: str
    latency_ms: int
    prompt_hash: str

    class Config:
        from_attributes = True


class AuditTurnResponse(BaseModel):
    """Turno completo, com o que o LLM pediu (`proposed_terms`) ao lado do
    que sobrou depois do mandato (`terms_after_policy`) — a resposta direta
    para 'por que o agente aceitou R$ X'."""

    id: str
    round_no: int
    actor: str
    intent: str
    proposed_terms: Dict[str, Any]
    terms_after_policy: Dict[str, Any]
    policy_violations: List[str]
    rationale: str
    message_text: str
    ai_call: Optional[AiCallSummary] = None
    created_at: datetime

    class Config:
        from_attributes = True


class NegotiationAuditResponse(BaseModel):
    negotiation_id: str
    state: str
    outcome: str
    outcome_reason: str
    turns: List[AuditTurnResponse]
