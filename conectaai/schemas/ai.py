# Arquivo: conectaai/schemas/ai.py
from typing import List, Optional

from pydantic import BaseModel

from conectaai.schemas.campaign import CampaignResponse
from conectaai.schemas.creator import CreatorResponse
from conectaai.schemas.mandate import DeliverableSpec, MandateResponse
from conectaai.schemas.opportunity import OpportunityResponse


class AiChatRequest(BaseModel):
    text: str
    context_creator_ids: Optional[List[str]] = None


class AiChatResponse(BaseModel):
    text: str
    creators: List[CreatorResponse] = []
    total_found: int = 0
    quick_replies: List[str] = []


class CreatorAiChatRequest(BaseModel):
    text: str
    context_opportunity_ids: Optional[List[str]] = None


class CreatorAiChatResponse(BaseModel):
    text: str
    opportunities: List[OpportunityResponse] = []
    total_found: int = 0
    quick_replies: List[str] = []


class CampaignDraftRequest(BaseModel):
    text: str


class CampaignDraftResponse(BaseModel):
    """Preview gerado a partir do briefing em texto livre — não persiste
    nada. O humano revisa (e pode editar) antes de confirmar em
    POST /ai/campaigns/confirm."""

    campaign_name: str
    objective: str
    target_count: int
    desired_categories: List[str] = []
    city: str = ""
    budget_total: float = 0
    ideal_price: float = 0
    price_ceiling: float = 0
    deliverables: List[DeliverableSpec] = []
    clarifying_question: str = ""
    source: str = "gemini"  # "gemini" | "heuristic" — o front avisa o usuário quando foi o caminho sem IA


class ConfirmCampaignDraftRequest(BaseModel):
    campaign_name: str
    objective: str = ""
    target_count: int = 1
    desired_categories: List[str] = []
    city: str = ""
    budget_total: float = 0
    ideal_price: float = 0
    price_ceiling: float = 0
    deliverables: List[DeliverableSpec] = []
    max_rounds: int = 4
    auto_approve_limit: float = 0


class ConfirmCampaignDraftResponse(BaseModel):
    campaign: CampaignResponse
    mandate: MandateResponse


class MatchCreatorsRequest(BaseModel):
    text: str
    limit: int = 10


class MatchedCreator(BaseModel):
    creator: CreatorResponse
    match_score: int
    match_reason: str


class MatchCreatorsResponse(BaseModel):
    text: str
    results: List[MatchedCreator]
    total_found: int
    source: str  # "semantic" | "heuristic"


class MatchOpportunitiesRequest(BaseModel):
    text: str
    limit: int = 10


class MatchedOpportunity(BaseModel):
    mandate_id: str
    campaign_id: Optional[str]
    company_id: str
    company_name: str
    objective: str
    ideal_price: float
    price_ceiling: float
    deliverables: List[DeliverableSpec]
    match_score: int
    match_reason: str


class MatchOpportunitiesResponse(BaseModel):
    text: str
    results: List[MatchedOpportunity]
    total_found: int
    source: str  # "semantic" | "heuristic"
