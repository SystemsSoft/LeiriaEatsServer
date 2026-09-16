# Arquivo: conectaai/api/routes/ai_routes.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, require_role
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.repositories.opportunity_repo import OpportunityRepository
from conectaai.schemas.ai import AiChatRequest, AiChatResponse, CreatorAiChatRequest, CreatorAiChatResponse
from conectaai.schemas.creator import MatchBreakdown, creator_to_response
from conectaai.schemas.opportunity import OpportunityResponse
from conectaai.services import ai_service

router = APIRouter(prefix="/ai", tags=["Assistente de IA"])


@router.post("/company/chat", response_model=AiChatResponse)
def company_chat(
    data: AiChatRequest,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    company = CompanyRepository.get_by_user_id(db, current_user.user_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    pool = (
        CreatorRepository.get_many_by_ids(db, data.context_creator_ids)
        if data.context_creator_ids
        else CreatorRepository.get_all(db)
    )
    text, creators, total_found = ai_service.company_chat(data.text, pool, company.desired_categories or [])

    creator_responses = []
    for creator in creators:
        score, breakdown, reason = ai_service.compute_creator_match(creator, company.desired_categories or [])
        response = creator_to_response(creator)
        response.match_score = score
        response.match_breakdown = MatchBreakdown(**breakdown)
        response.match_reason = reason
        creator_responses.append(response)

    return AiChatResponse(
        text=text,
        creators=creator_responses,
        total_found=total_found,
        quick_replies=ai_service.COMPANY_DEFAULT_QUICK_REPLIES,
    )


@router.post("/creator/chat", response_model=CreatorAiChatResponse)
def creator_chat(
    data: CreatorAiChatRequest,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: Session = Depends(get_db),
):
    creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Perfil de creator não encontrado")

    pool = (
        OpportunityRepository.get_many_by_ids(db, data.context_opportunity_ids)
        if data.context_opportunity_ids
        else OpportunityRepository.get_all(db)
    )
    text, opportunities, total_found = ai_service.creator_chat(data.text, pool, creator)

    opportunity_responses = []
    for opportunity in opportunities:
        response = OpportunityResponse.model_validate(opportunity)
        response.match_score = ai_service.compute_opportunity_match(opportunity, creator)
        opportunity_responses.append(response)

    return CreatorAiChatResponse(
        text=text,
        opportunities=opportunity_responses,
        total_found=total_found,
        quick_replies=ai_service.CREATOR_DEFAULT_QUICK_REPLIES,
    )
