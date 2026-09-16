# Arquivo: conectaai/repositories/conversation_repo.py
from typing import List, Optional

from sqlalchemy.orm import Session, joinedload

from conectaai.models.sql_models import ConversationDB, MessageDB


class ConversationRepository:
    @staticmethod
    def get_all_for_user(db: Session, *, company_id: Optional[str], creator_id: Optional[str]) -> List[ConversationDB]:
        query = db.query(ConversationDB).options(joinedload(ConversationDB.messages))
        if company_id:
            query = query.filter(ConversationDB.company_id == company_id)
        if creator_id:
            query = query.filter(ConversationDB.creator_id == creator_id)
        return query.all()

    @staticmethod
    def get_by_id(db: Session, conversation_id: str) -> Optional[ConversationDB]:
        return (
            db.query(ConversationDB)
            .options(joinedload(ConversationDB.messages))
            .filter(ConversationDB.id == conversation_id)
            .first()
        )

    @staticmethod
    def add_message(db: Session, conversation: ConversationDB, sender_id: str, text: str) -> ConversationDB:
        db.add(MessageDB(conversation_id=conversation.id, sender_id=sender_id, text=text))
        db.commit()
        db.refresh(conversation)
        return conversation
