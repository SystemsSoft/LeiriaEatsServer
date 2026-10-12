# Arquivo: conectaai/repositories/negotiation_turn_repo.py
from typing import List

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from conectaai.models.sql_models import NegotiationTurnDB


class NegotiationTurnRepository:
    @staticmethod
    def get_all_for_negotiation(db: Session, negotiation_id: str) -> List[NegotiationTurnDB]:
        return (
            db.query(NegotiationTurnDB)
            .filter(NegotiationTurnDB.negotiation_id == negotiation_id)
            .order_by(NegotiationTurnDB.created_at.asc())
            .all()
        )

    @staticmethod
    def create(db: Session, data: dict) -> NegotiationTurnDB:
        """A UniqueConstraint(negotiation_id, round_no, actor) é a rede de
        segurança final contra turno duplicado (ex.: dois workers processando
        a mesma negociação por uma falha de lease) — se isso disparar, a
        chamada propaga IntegrityError e quem chamou deve tratar como turno
        já processado, não como erro."""
        turn = NegotiationTurnDB(**data)
        db.add(turn)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise
        db.refresh(turn)
        return turn
