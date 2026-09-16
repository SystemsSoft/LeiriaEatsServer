# Arquivo: conectaai/schemas/ai.py
from typing import List, Optional

from pydantic import BaseModel

from conectaai.schemas.creator import CreatorResponse
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
