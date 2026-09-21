# Arquivo: conectaai/repositories/negotiation_repo.py
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session, joinedload

from conectaai.models.sql_models import NegotiationDB


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NegotiationRepository:
    @staticmethod
    def get_by_id(db: Session, negotiation_id: str) -> Optional[NegotiationDB]:
        return (
            db.query(NegotiationDB)
            .options(joinedload(NegotiationDB.turns), joinedload(NegotiationDB.agreement))
            .filter(NegotiationDB.id == negotiation_id)
            .first()
        )

    @staticmethod
    def get_all_for_user(db: Session, *, company_id: Optional[str], creator_id: Optional[str]) -> List[NegotiationDB]:
        query = db.query(NegotiationDB).options(joinedload(NegotiationDB.agreement))
        if company_id:
            query = query.filter(NegotiationDB.company_id == company_id)
        if creator_id:
            query = query.filter(NegotiationDB.creator_id == creator_id)
        return query.order_by(NegotiationDB.updated_at.desc()).all()

    @staticmethod
    def create(db: Session, data: dict) -> NegotiationDB:
        negotiation = NegotiationDB(**data)
        db.add(negotiation)
        db.commit()
        db.refresh(negotiation)
        return negotiation

    @staticmethod
    def update(db: Session, negotiation: NegotiationDB, data: dict) -> NegotiationDB:
        for key, value in data.items():
            setattr(negotiation, key, value)
        db.commit()
        db.refresh(negotiation)
        return negotiation

    @staticmethod
    def acquire_lease(db: Session, negotiation_id: str, *, lease_owner: str, lease_seconds: int) -> Optional[NegotiationDB]:
        """Trava a linha (SELECT ... FOR UPDATE) e só devolve a negociação se
        ela está livre para processar agora — estado `queued`, ou `running`
        com lease vencido (processo anterior morreu no meio). Evita dois
        workers rodando o mesmo turno ao mesmo tempo; a UniqueConstraint em
        NegotiationTurnDB é a rede de segurança final caso isso falhe."""
        negotiation = (
            db.query(NegotiationDB).filter(NegotiationDB.id == negotiation_id).with_for_update().first()
        )
        if negotiation is None:
            return None

        now = _now()
        lease_free = negotiation.lease_expires_at is None or negotiation.lease_expires_at <= now
        if negotiation.state not in ("queued", "running") or not lease_free:
            db.rollback()
            return None

        negotiation.state = "running"
        negotiation.lease_owner = lease_owner
        negotiation.lease_expires_at = now + timedelta(seconds=lease_seconds)
        db.commit()
        db.refresh(negotiation)
        return negotiation

    @staticmethod
    def release_lease(db: Session, negotiation: NegotiationDB) -> None:
        negotiation.lease_owner = None
        negotiation.lease_expires_at = None
        db.commit()

    @staticmethod
    def sweep_expired_leases(db: Session) -> int:
        """Roda no startup: qualquer negociação presa em `running` com lease
        vencido (o processo anterior morreu — ex.: restart de deploy) volta
        para `queued` para ser retomada por /resume ou pelo próximo request."""
        now = _now()
        count = (
            db.query(NegotiationDB)
            .filter(NegotiationDB.state == "running", NegotiationDB.lease_expires_at < now)
            .update({"state": "queued", "lease_owner": None, "lease_expires_at": None})
        )
        db.commit()
        return count
