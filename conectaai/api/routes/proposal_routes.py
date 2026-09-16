# Arquivo: conectaai/api/routes/proposal_routes.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, get_current_user, require_role
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.repositories.proposal_repo import ProposalRepository
from conectaai.schemas.proposal import ProposalCreateRequest, ProposalResponse, ProposalUpdateRequest

router = APIRouter(prefix="/proposals", tags=["Propostas"])


@router.get("", response_model=List[ProposalResponse])
def list_proposals(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    company_id = None
    creator_id = None
    if current_user.role == "company":
        company = CompanyRepository.get_by_user_id(db, current_user.user_id)
        company_id = company.id if company else None
    else:
        creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
        creator_id = creator.id if creator else None
    return ProposalRepository.get_all_for_user(db, company_id=company_id, creator_id=creator_id)


@router.post("", response_model=ProposalResponse, status_code=201)
def create_proposal(
    data: ProposalCreateRequest,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    company = CompanyRepository.get_by_user_id(db, current_user.user_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return ProposalRepository.create(db, company.id, data.dict())


@router.put("/{proposal_id}", response_model=ProposalResponse)
def update_proposal(
    proposal_id: str,
    data: ProposalUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    proposal = ProposalRepository.get_by_id(db, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    return ProposalRepository.update(db, proposal, data.dict(exclude_unset=True))
