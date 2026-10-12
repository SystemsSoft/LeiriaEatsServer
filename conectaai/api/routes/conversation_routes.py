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
from conectaai.schemas.conversation import (
    ChatMessageResponse,
    ConversationResponse,
    SendMessageRequest,
    StartConversationRequest,
)

router = APIRouter(prefix="/conversations", tags=["Conversas"])


def _self_id(db: Session, current_user: CurrentUser) -> str:
    if current_user.role == "company":
        company = CompanyRepository.get_by_user_id(db, current_user.user_id)
        return company.id if company else ""
    creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
    return creator.id if creator else ""


def _require_participant(conversation: ConversationDB, current_user: CurrentUser, self_id: str) -> None:
    """Garante que quem está autenticado é o creator ou a empresa dessa
    conversa específica — sem isso, qualquer conta logada que soubesse o id
    de uma conversa alheia conseguiria ler ou escrever nela."""
    own_id = conversation.creator_id if current_user.role == "creator" else conversation.company_id
    if own_id != self_id:
        raise HTTPException(status_code=403, detail="Você não faz parte dessa conversa")


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
    self_id = _self_id(db, current_user)
    _require_participant(conversation, current_user, self_id)
    return _to_response(conversation, self_id)


@router.post("", response_model=ConversationResponse, status_code=201)
def start_conversation(
    data: StartConversationRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    self_id = _self_id(db, current_user)
    # O id de quem está chamando vem do token, não do body — impede que uma
    # empresa/creator crie uma conversa se passando por outra pessoa só
    # informando o id de outro usuário no lugar do próprio.
    if current_user.role == "creator" and data.creator_id != self_id:
        raise HTTPException(status_code=403, detail="Não autorizado")
    if current_user.role == "company" and data.company_id != self_id:
        raise HTTPException(status_code=403, detail="Não autorizado")

    conversation = ConversationRepository.get_or_create(
        db,
        creator_id=data.creator_id,
        company_id=data.company_id,
        campaign_id=data.campaign_id,
        campaign_name=data.campaign_name,
    )
    return _to_response(conversation, self_id)


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
    _require_participant(conversation, current_user, self_id)
    conversation = ConversationRepository.add_message(db, conversation, self_id, data.text)
    return _to_response(conversation, self_id)
