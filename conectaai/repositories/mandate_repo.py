# Arquivo: conectaai/repositories/mandate_repo.py
from typing import List, Optional

from sqlalchemy.orm import Session

from conectaai.models.sql_models import CommercialMandateDB


class MandateRepository:
    @staticmethod
    def get_by_id(db: Session, mandate_id: str) -> Optional[CommercialMandateDB]:
        return db.query(CommercialMandateDB).filter(CommercialMandateDB.id == mandate_id).first()

    @staticmethod
    def get_active_for_owner(
        db: Session, *, owner_type: str, owner_id: str, campaign_id: Optional[str] = None
    ) -> Optional[CommercialMandateDB]:
        return (
            db.query(CommercialMandateDB)
            .filter(
                CommercialMandateDB.owner_type == owner_type,
                CommercialMandateDB.owner_id == owner_id,
                CommercialMandateDB.campaign_id == campaign_id,
                CommercialMandateDB.active.is_(True),
            )
            .order_by(CommercialMandateDB.created_at.desc())
            .first()
        )

    @staticmethod
    def get_all_for_owner(db: Session, *, owner_type: str, owner_id: str) -> List[CommercialMandateDB]:
        return (
            db.query(CommercialMandateDB)
            .filter(CommercialMandateDB.owner_type == owner_type, CommercialMandateDB.owner_id == owner_id)
            .order_by(CommercialMandateDB.created_at.desc())
            .all()
        )

    @staticmethod
    def create(db: Session, *, owner_type: str, owner_id: str, data: dict) -> CommercialMandateDB:
        # Um mandato novo para o mesmo dono (mesma campanha, ou o mandato
        # global quando campaign_id é None) substitui o anterior — mas o
        # anterior fica no banco com active=False, nunca é apagado, pra
        # manter a trilha de auditoria de "o que o agente podia fazer" ao
        # longo do tempo.
        db.query(CommercialMandateDB).filter(
            CommercialMandateDB.owner_type == owner_type,
            CommercialMandateDB.owner_id == owner_id,
            CommercialMandateDB.campaign_id == data.get("campaign_id"),
            CommercialMandateDB.active.is_(True),
        ).update({"active": False})

        mandate = CommercialMandateDB(owner_type=owner_type, owner_id=owner_id, active=True, **data)
        db.add(mandate)
        db.commit()
        db.refresh(mandate)
        return mandate

    @staticmethod
    def deactivate(db: Session, mandate: CommercialMandateDB) -> CommercialMandateDB:
        mandate.active = False
        db.commit()
        db.refresh(mandate)
        return mandate
