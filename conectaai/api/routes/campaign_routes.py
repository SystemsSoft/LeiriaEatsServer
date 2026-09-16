# Arquivo: conectaai/api/routes/campaign_routes.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, require_role
from conectaai.models.sql_models import CampaignDB
from conectaai.repositories.campaign_repo import CampaignRepository
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.schemas.campaign import CampaignCreateRequest, CampaignResponse, CampaignUpdateRequest

router = APIRouter(prefix="/campaigns", tags=["Campanhas"])


def _to_response(campaign: CampaignDB) -> CampaignResponse:
    return CampaignResponse(
        id=campaign.id,
        name=campaign.name,
        company_id=campaign.company_id,
        creator_ids=CampaignRepository.creator_ids_of(campaign),
        budget=campaign.budget,
        status=campaign.status,
        deadline=campaign.deadline,
        description=campaign.description,
        progress=campaign.progress,
        current_stage=campaign.current_stage,
        content_types=campaign.content_types or [],
    )


def _my_company(db: Session, current_user: CurrentUser):
    company = CompanyRepository.get_by_user_id(db, current_user.user_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return company


@router.get("", response_model=List[CampaignResponse])
def list_campaigns(
    current_user: CurrentUser = Depends(require_role("company")), db: Session = Depends(get_db)
):
    company = _my_company(db, current_user)
    campaigns = CampaignRepository.get_all_by_company(db, company.id)
    return [_to_response(c) for c in campaigns]


@router.post("", response_model=CampaignResponse, status_code=201)
def create_campaign(
    data: CampaignCreateRequest,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    company = _my_company(db, current_user)
    payload = data.dict(exclude={"creator_ids"})
    campaign = CampaignRepository.create(db, company.id, payload, data.creator_ids)
    return _to_response(campaign)


@router.get("/{campaign_id}", response_model=CampaignResponse)
def get_campaign(campaign_id: str, db: Session = Depends(get_db)):
    campaign = CampaignRepository.get_by_id(db, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")
    return _to_response(campaign)


@router.put("/{campaign_id}", response_model=CampaignResponse)
def update_campaign(
    campaign_id: str,
    data: CampaignUpdateRequest,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    campaign = CampaignRepository.get_by_id(db, campaign_id)
    if not campaign or campaign.company_id != CompanyRepository.get_by_user_id(db, current_user.user_id).id:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")

    update_data = data.dict(exclude_unset=True, exclude={"creator_ids"})
    campaign = CampaignRepository.update(db, campaign, update_data, data.creator_ids)
    return _to_response(campaign)
