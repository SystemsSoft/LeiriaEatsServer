# Arquivo: conectaai/api/routes/conversation_routes.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, get_current_user
from conectaai.models.sql_models import ConversationDB
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.conversation_repo import ConversationRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.schemas.conversation import ChatMessageResponse, ConversationResponse, SendMessageRequest

router = APIRouter(prefix="/conversations", tags=["Conversas"])


def _self_id(db: Session, current_user: CurrentUser) -> str:
    if current_user.role == "company":
        company = CompanyRepository.get_by_user_id(db, current_user.user_id)
        return company.id if company else ""
    creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
    return creator.id if creator else ""


def _to_response(conversation: ConversationDB, self_id: str) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        creator_id=conversation.creator_id,
        company_id=conversation.company_id,
        campaign_id=conversation.campaign_id,
        campaign_name=conversation.campaign_name,
        status=conversation.status,
        value=conversation.value,
        messages=[
            ChatMessageResponse(
                id=m.id,
                sender_id=m.sender_id,
                text=m.text,
                timestamp=m.timestamp,
                is_me=(m.sender_id == self_id),
            )
            for m in conversation.messages
        ],
    )


@router.get("", response_model=List[ConversationResponse])
def list_conversations(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    self_id = _self_id(db, current_user)
    company_id = self_id if current_user.role == "company" else None
    creator_id = self_id if current_user.role == "creator" else None
    conversations = ConversationRepository.get_all_for_user(db, company_id=company_id, creator_id=creator_id)
    return [_to_response(c, self_id) for c in conversations]


@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
):
    conversation = ConversationRepository.get_by_id(db, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    return _to_response(conversation, _self_id(db, current_user))


@router.post("/{conversation_id}/messages", response_model=ConversationResponse)
def send_message(
    conversation_id: str,
    data: SendMessageRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = ConversationRepository.get_by_id(db, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    self_id = _self_id(db, current_user)
    conversation = ConversationRepository.add_message(db, conversation, self_id, data.text)
    return _to_response(conversation, self_id)
