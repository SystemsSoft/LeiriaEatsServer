# Arquivo: conectaai/api/routes/proposal_routes.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, get_current_user, require_role
from conectaai.repositories.campaign_repo import CampaignRepository
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.repositories.notification_repo import NotificationRepository
from conectaai.repositories.proposal_repo import ProposalRepository
from conectaai.schemas.proposal import (
    CreatorProposalCreateRequest,
    ProposalCreateRequest,
    ProposalResponse,
    ProposalUpdateRequest,
)

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
    # Sem esta checagem, um creator_id desconhecido só estourava na FK do MySQL (erro 500).
    creator = CreatorRepository.get_by_id(db, data.creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator não encontrado")
    if data.campaign_id:
        campaign = CampaignRepository.get_by_id(db, data.campaign_id)
        if not campaign or campaign.company_id != company.id:
            raise HTTPException(status_code=400, detail="Campanha inválida")
    proposal = ProposalRepository.create(db, company.id, data.dict())
    # Proposta enviada é o primeiro passo da conversa comercial: tira a campanha de "rascunho".
    CampaignRepository.mark_negotiation_started(db, data.campaign_id)

    NotificationRepository.create(
        db,
        user_id=creator.user_id,
        type_="proposal",
        title="Nova proposta recebida",
        message=f'{company.name} te enviou uma proposta para "{data.campaign_name or "uma campanha"}".',
    )
    return proposal


@router.post("/from-creator", response_model=ProposalResponse, status_code=201)
def create_proposal_from_creator(
    data: CreatorProposalCreateRequest,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: Session = Depends(get_db),
):
    """O creator se candidata a uma campanha de uma empresa: a proposta chega para a empresa
    (aba "Recebidas"), que aceita ou recusa. Aceitar vincula o creator e ativa a campanha."""
    creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator não encontrado")

    campaign = CampaignRepository.get_by_id(db, data.campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")
    if campaign.status == "completed":
        raise HTTPException(status_code=400, detail="Essa campanha já foi concluída")
    if creator.id in CampaignRepository.creator_ids_of(campaign):
        raise HTTPException(status_code=400, detail="Você já faz parte dessa campanha")
    if ProposalRepository.get_pending_from_creator(db, creator_id=creator.id, campaign_id=campaign.id):
        raise HTTPException(status_code=400, detail="Você já enviou uma proposta para essa campanha — aguarde a resposta da empresa")

    proposal = ProposalRepository.create(
        db,
        campaign.company_id,
        {
            "creator_id": creator.id,
            "campaign_id": campaign.id,
            "campaign_name": campaign.name,
            "content_type": data.content_type,
            "quantity": data.quantity,
            "deadline": data.deadline,
            "description": data.description,
            "budget": data.budget,
            "message": data.message,
            "sender_role": "creator",
        },
    )

    company = CompanyRepository.get_by_id(db, campaign.company_id)
    if company:
        NotificationRepository.create(
            db,
            user_id=company.user_id,
            type_="proposal",
            title="Nova proposta de creator",
            message=f'{creator.name} quer participar da campanha "{campaign.name}".',
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

    # Só quem RECEBEU a proposta responde — sem essa checagem, qualquer token válido poderia
    # alterar o status de uma proposta alheia só sabendo o id. Quem recebe depende de quem enviou:
    # convite da empresa -> responde o creator; candidatura do creator -> responde a empresa.
    sent_by_creator = proposal.sender_role == "creator"
    if data.status is not None:
        if data.status not in ("accepted", "rejected"):
            raise HTTPException(status_code=400, detail="Status inválido")
        receiver_role = "company" if sent_by_creator else "creator"
        if current_user.role != receiver_role:
            raise HTTPException(
                status_code=403,
                detail="Só a empresa pode aceitar ou recusar essa proposta" if sent_by_creator else "Só o creator pode aceitar ou recusar uma proposta",
            )
        if sent_by_creator:
            company = CompanyRepository.get_by_user_id(db, current_user.user_id)
            owns = company is not None and company.id == proposal.company_id
        else:
            creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
            owns = creator is not None and creator.id == proposal.creator_id
        if not owns:
            raise HTTPException(status_code=403, detail="Essa proposta não é sua")
        if proposal.status != "pending":
            raise HTTPException(status_code=400, detail="Essa proposta já foi respondida")

    updated = ProposalRepository.update(db, proposal, data.dict(exclude_unset=True))

    if data.status == "accepted":
        CampaignRepository.mark_proposal_accepted(db, updated.campaign_id, updated.creator_id)

    if data.status in ("accepted", "rejected"):
        # Avisa quem ENVIOU a proposta (o outro lado de quem respondeu).
        if sent_by_creator:
            recipient = CreatorRepository.get_by_id(db, updated.creator_id)
            who = "A empresa"
        else:
            recipient = CompanyRepository.get_by_id(db, updated.company_id)
            who = "O creator"
        if recipient:
            accepted = data.status == "accepted"
            NotificationRepository.create(
                db,
                user_id=recipient.user_id,
                type_="proposalAccepted" if accepted else "proposal",
                title="Proposta aceita!" if accepted else "Proposta recusada",
                message=f'{who} {"aceitou" if accepted else "recusou"} a proposta "{updated.campaign_name or "da campanha"}".',
            )
    return updated
