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

    @staticmethod
    def creator_ids_of(campaign: CampaignDB) -> List[str]:
        return [link.creator_id for link in campaign.creator_links]
