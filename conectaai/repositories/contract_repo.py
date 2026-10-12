# Arquivo: conectaai/repositories/contract_repo.py
from typing import List, Optional

from sqlalchemy.orm import Session

from conectaai.models.sql_models import ContractDB


class ContractRepository:
    @staticmethod
    def get_by_id(db: Session, contract_id: str) -> Optional[ContractDB]:
        return db.query(ContractDB).filter(ContractDB.id == contract_id).first()

    @staticmethod
    def get_by_proposal_id(db: Session, proposal_id: str) -> Optional[ContractDB]:
        return db.query(ContractDB).filter(ContractDB.proposal_id == proposal_id).first()

    @staticmethod
    def get_all_for_user(db: Session, *, company_id: Optional[str], creator_id: Optional[str]) -> List[ContractDB]:
        query = db.query(ContractDB)
        if company_id:
            query = query.filter(ContractDB.company_id == company_id)
        if creator_id:
            query = query.filter(ContractDB.creator_id == creator_id)
        return query.order_by(ContractDB.created_at.desc()).all()

    @staticmethod
    def create(db: Session, data: dict) -> ContractDB:
        contract = ContractDB(**data)
        db.add(contract)
        db.commit()
        db.refresh(contract)
        return contract

    @staticmethod
    def update(db: Session, contract: ContractDB, data: dict) -> ContractDB:
        for key, value in data.items():
            setattr(contract, key, value)
        db.commit()
        db.refresh(contract)
        return contract
