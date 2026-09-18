# Arquivo: conectaai/schemas/conversation.py
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ChatMessageResponse(BaseModel):
    id: str
    sender_id: str
    text: str
    timestamp: datetime
    is_me: bool  # calculado por requisição, comparando sender_id com o usuário autenticado

    class Config:
        from_attributes = True


class SendMessageRequest(BaseModel):
    text: str


class StartConversationRequest(BaseModel):
    creator_id: str
    company_id: str
    campaign_id: Optional[str] = None
    campaign_name: str = ""


class ConversationResponse(BaseModel):
    id: str
    creator_id: str
    company_id: str
    campaign_id: Optional[str]
    campaign_name: str
    status: str
    value: Optional[float]
    messages: List[ChatMessageResponse]

    class Config:
        from_attributes = True
