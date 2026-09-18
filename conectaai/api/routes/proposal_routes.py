# Arquivo: conectaai/api/routes/proposal_routes.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, get_current_user, require_role
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.repositories.notification_repo import NotificationRepository
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
    proposal = ProposalRepository.create(db, company.id, data.dict())

    creator = CreatorRepository.get_by_id(db, data.creator_id)
    if creator:
        NotificationRepository.create(
            db,
            user_id=creator.user_id,
            type_="proposal",
            title="Nova proposta recebida",
            message=f'{company.name} te enviou uma proposta para "{data.campaign_name or "uma campanha"}".',
        )
    return proposal


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

    # Só quem recebeu a proposta pode aceitar/recusar — sem essa checagem,
    # qualquer token válido (de qualquer creator) poderia alterar o status de
    # uma proposta de outra pessoa só sabendo o id.
    if data.status is not None:
        if data.status not in ("accepted", "rejected"):
            raise HTTPException(status_code=400, detail="Status inválido")
        if current_user.role != "creator":
            raise HTTPException(status_code=403, detail="Só o creator pode aceitar ou recusar uma proposta")
        creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
        if not creator or creator.id != proposal.creator_id:
            raise HTTPException(status_code=403, detail="Essa proposta não é sua")

    updated = ProposalRepository.update(db, proposal, data.dict(exclude_unset=True))

    if data.status in ("accepted", "rejected"):
        company = CompanyRepository.get_by_id(db, updated.company_id)
        if company:
            if data.status == "accepted":
                NotificationRepository.create(
                    db,
                    user_id=company.user_id,
                    type_="proposalAccepted",
                    title="Proposta aceita!",
                    message=f'O creator aceitou a proposta "{updated.campaign_name or "sua campanha"}".',
                )
            else:
                NotificationRepository.create(
                    db,
                    user_id=company.user_id,
                    type_="proposal",
                    title="Proposta recusada",
                    message=f'O creator recusou a proposta "{updated.campaign_name or "sua campanha"}".',
                )
    return updated
