# Arquivo: conectaai/repositories/payment_repo.py
from typing import List, Optional

from sqlalchemy.orm import Session

from conectaai.models.sql_models import PaymentDB


class PaymentRepository:
    @staticmethod
    def get_by_proposal(db: Session, proposal_id: str) -> Optional[PaymentDB]:
        return db.query(PaymentDB).filter(PaymentDB.proposal_id == proposal_id).first()

    @staticmethod
    def get_by_session(db: Session, session_id: str) -> Optional[PaymentDB]:
        if not session_id:
            return None
        return db.query(PaymentDB).filter(PaymentDB.checkout_session_id == session_id).first()

    @staticmethod
    def list_for(db: Session, *, role: str, profile_id: str) -> List[PaymentDB]:
        column = PaymentDB.company_id if role == "company" else PaymentDB.creator_id
        return db.query(PaymentDB).filter(column == profile_id).order_by(PaymentDB.created_at.desc()).all()

    @staticmethod
    def statuses_for(db: Session, proposal_ids: List[str]) -> dict:
        if not proposal_ids:
            return {}
        rows = db.query(PaymentDB.proposal_id, PaymentDB.status).filter(PaymentDB.proposal_id.in_(proposal_ids)).all()
        return {proposal_id: status for proposal_id, status in rows}
