# Arquivo: conectaai/repositories/proposal_repo.py
from typing import List, Optional

from sqlalchemy.orm import Session

from conectaai.models.sql_models import ProposalDB


class ProposalRepository:
    @staticmethod
    def get_all_for_user(db: Session, *, company_id: Optional[str], creator_id: Optional[str]) -> List[ProposalDB]:
        query = db.query(ProposalDB)
        if company_id:
            query = query.filter(ProposalDB.company_id == company_id)
        if creator_id:
            query = query.filter(ProposalDB.creator_id == creator_id)
        return query.all()

    @staticmethod
    def get_by_id(db: Session, proposal_id: str) -> Optional[ProposalDB]:
        return db.query(ProposalDB).filter(ProposalDB.id == proposal_id).first()

    @staticmethod
    def create(db: Session, company_id: str, data: dict) -> ProposalDB:
        proposal = ProposalDB(company_id=company_id, status="pending", **data)
        db.add(proposal)
        db.commit()
        db.refresh(proposal)
        return proposal

    @staticmethod
    def update(db: Session, proposal: ProposalDB, data: dict) -> ProposalDB:
        for key, value in data.items():
            setattr(proposal, key, value)
        db.commit()
        db.refresh(proposal)
        return proposal
