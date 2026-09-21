# Arquivo: conectaai/api/routes/agreement_routes.py
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from conectaai.api.deps import self_id
from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, get_current_user
from conectaai.models.sql_models import CampaignCreatorDB
from conectaai.repositories.agreement_repo import AgreementRepository
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.repositories.notification_repo import NotificationRepository
from conectaai.repositories.proposal_repo import ProposalRepository
from conectaai.schemas.agreement import AgreementResponse, RejectAgreementRequest

router = APIRouter(prefix="/agreements", tags=["Acordos"])


def _now():
    return datetime.now(timezone.utc)


def _require_participant(agreement, current_user: CurrentUser, my_id: str) -> None:
    own_id = agreement.creator_id if current_user.role == "creator" else agreement.company_id
    if own_id != my_id:
        raise HTTPException(status_code=403, detail="Esse acordo não é seu")


def _finalize_if_both_approved(db: Session, agreement):
    if not (agreement.company_approved_at and agreement.creator_approved_at):
        return agreement

    terms = agreement.terms or {}
    deliverables = terms.get("deliverables") or []
    proposal = ProposalRepository.create(
        db,
        agreement.company_id,
        {
            "creator_id": agreement.creator_id,
            "campaign_id": agreement.campaign_id,
            "campaign_name": "",
            "content_type": ", ".join(d.get("content_type", "") for d in deliverables) if deliverables else "",
            "quantity": sum(d.get("quantity", 0) for d in deliverables) or 1,
            "budget": agreement.total_value,
            "description": "Gerado automaticamente a partir de um acordo negociado por agentes de IA — revisar antes de qualquer execução.",
            "message": "",
        },
    )
    proposal = ProposalRepository.update(db, proposal, {"status": "accepted"})

    if agreement.campaign_id:
        exists = (
            db.query(CampaignCreatorDB)
            .filter(CampaignCreatorDB.campaign_id == agreement.campaign_id, CampaignCreatorDB.creator_id == agreement.creator_id)
            .first()
        )
        if not exists:
            db.add(CampaignCreatorDB(campaign_id=agreement.campaign_id, creator_id=agreement.creator_id))
            db.commit()

    agreement = AgreementRepository.update(db, agreement, {"status": "approved", "proposal_id": proposal.id})

    company = CompanyRepository.get_by_id(db, agreement.company_id)
    creator = CreatorRepository.get_by_id(db, agreement.creator_id)
    if company:
        NotificationRepository.create(
            db, user_id=company.user_id, type_="proposalAccepted", title="Acordo aprovado!",
            message="Os dois lados aprovaram o acordo negociado pelos agentes.",
        )
    if creator:
        NotificationRepository.create(
            db, user_id=creator.user_id, type_="proposalAccepted", title="Acordo aprovado!",
            message="Os dois lados aprovaram o acordo negociado pelos agentes.",
        )
    return agreement


@router.get("", response_model=List[AgreementResponse])
def list_agreements(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    my_id = self_id(db, current_user)
    company_id = my_id if current_user.role == "company" else None
    creator_id = my_id if current_user.role == "creator" else None
    return AgreementRepository.get_all_for_user(db, company_id=company_id, creator_id=creator_id)


@router.get("/{agreement_id}", response_model=AgreementResponse)
def get_agreement(agreement_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    agreement = AgreementRepository.get_by_id(db, agreement_id)
    if not agreement:
        raise HTTPException(status_code=404, detail="Acordo não encontrado")
    _require_participant(agreement, current_user, self_id(db, current_user))
    return agreement


@router.post("/{agreement_id}/approve", response_model=AgreementResponse)
def approve_agreement(agreement_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    agreement = AgreementRepository.get_by_id(db, agreement_id)
    if not agreement:
        raise HTTPException(status_code=404, detail="Acordo não encontrado")
    my_id = self_id(db, current_user)
    _require_participant(agreement, current_user, my_id)
    if agreement.status != "awaiting_approval":
        raise HTTPException(status_code=400, detail="Esse acordo já foi decidido")

    field = "company_approved_at" if current_user.role == "company" else "creator_approved_at"
    agreement = AgreementRepository.update(db, agreement, {field: _now()})
    return _finalize_if_both_approved(db, agreement)


@router.post("/{agreement_id}/reject", response_model=AgreementResponse)
def reject_agreement(
    agreement_id: str, data: RejectAgreementRequest, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
):
    agreement = AgreementRepository.get_by_id(db, agreement_id)
    if not agreement:
        raise HTTPException(status_code=404, detail="Acordo não encontrado")
    my_id = self_id(db, current_user)
    _require_participant(agreement, current_user, my_id)
    if agreement.status != "awaiting_approval":
        raise HTTPException(status_code=400, detail="Esse acordo já foi decidido")

    agreement = AgreementRepository.update(
        db, agreement, {"status": "rejected", "rejected_by": current_user.role, "reject_reason": data.reason}
    )

    other = CreatorRepository.get_by_id(db, agreement.creator_id) if current_user.role == "company" else CompanyRepository.get_by_id(db, agreement.company_id)
    if other:
        NotificationRepository.create(
            db, user_id=other.user_id, type_="proposal", title="Acordo recusado",
            message=data.reason or "O acordo negociado pelos agentes foi recusado.",
        )
    return agreement
