# Arquivo: conectaai/repositories/campaign_repo.py
from typing import List, Optional

from sqlalchemy.orm import Session, joinedload

from conectaai.models.sql_models import CampaignCreatorDB, CampaignDB


class CampaignRepository:
    @staticmethod
    def get_all_by_company(db: Session, company_id: str) -> List[CampaignDB]:
        return (
            db.query(CampaignDB)
            .options(joinedload(CampaignDB.creator_links))
            .filter(CampaignDB.company_id == company_id)
            .all()
        )

    @staticmethod
    def get_by_id(db: Session, campaign_id: str) -> Optional[CampaignDB]:
        return (
            db.query(CampaignDB)
            .options(joinedload(CampaignDB.creator_links))
            .filter(CampaignDB.id == campaign_id)
            .first()
        )

    @staticmethod
    def create(db: Session, company_id: str, data: dict, creator_ids: List[str]) -> CampaignDB:
        campaign = CampaignDB(company_id=company_id, **data)
        db.add(campaign)
        db.flush()
        for creator_id in creator_ids:
            db.add(CampaignCreatorDB(campaign_id=campaign.id, creator_id=creator_id))
        db.commit()
        db.refresh(campaign)
        return campaign

    @staticmethod
    def update(db: Session, campaign: CampaignDB, data: dict, creator_ids: Optional[List[str]]) -> CampaignDB:
        for key, value in data.items():
            setattr(campaign, key, value)

        if creator_ids is not None:
            db.query(CampaignCreatorDB).filter(CampaignCreatorDB.campaign_id == campaign.id).delete()
            for creator_id in creator_ids:
                db.add(CampaignCreatorDB(campaign_id=campaign.id, creator_id=creator_id))

        db.commit()
        db.refresh(campaign)
        return campaign

    # Ordem das etapas do andamento da campanha (espelha CampaignStage no app).
    _STAGES = ["briefing", "negotiation", "contract", "production", "approval", "publication", "payment"]

    @staticmethod
    def _advance(db: Session, campaign_id: Optional[str], *, from_statuses: set, to_status: str, to_stage: str) -> None:
        """Avança uma campanha de `from_statuses` para `to_status`/`to_stage` — e SÓ nesse caso.
        Uma campanha que já está adiante (ativa, concluída, etapa mais à frente) nunca regride
        porque outra negociação começou ou outro acordo fechou."""
        if not campaign_id:
            return
        campaign = db.query(CampaignDB).filter(CampaignDB.id == campaign_id).first()
        if campaign is None or campaign.status not in from_statuses:
            return
        stages = CampaignRepository._STAGES
        current = campaign.current_stage if campaign.current_stage in stages else "briefing"
        campaign.status = to_status
        if stages.index(to_stage) > stages.index(current):
            campaign.current_stage = to_stage
        db.commit()

    @staticmethod
    def mark_negotiation_started(db: Session, campaign_id: Optional[str]) -> None:
        """Um agente começou a negociar por esta campanha: sai de "rascunho"."""
        CampaignRepository._advance(db, campaign_id, from_statuses={"draft"}, to_status="negotiating", to_stage="negotiation")

    @staticmethod
    def mark_agreement_approved(db: Session, campaign_id: Optional[str]) -> None:
        """Os dois lados aprovaram um acordo: a campanha tem creator contratado e vira "ativa"."""
        CampaignRepository._advance(db, campaign_id, from_statuses={"draft", "negotiating"}, to_status="active", to_stage="contract")

    @staticmethod
    def creator_ids_of(campaign: CampaignDB) -> List[str]:
        return [link.creator_id for link in campaign.creator_links]
