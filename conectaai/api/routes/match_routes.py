# Arquivo: conectaai/api/routes/match_routes.py
#
# Busca bilateral: a empresa descreve o que procura e recebe creators
# rankeados por semelhança semântica com o próprio perfil do creator; o
# creator descreve o que procura e recebe mandatos de campanha (empresas
# com campanha ativa) rankeados do mesmo jeito. Quando a API de embeddings
# não está disponível (sem chave, ou a chamada falha), cai automaticamente
# para um ranking por palavra-chave — nunca devolve erro só porque a IA
# está fora do ar.
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, require_role
from conectaai.models.sql_models import CommercialMandateDB
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.schemas.ai import (
    MatchCreatorsRequest,
    MatchCreatorsResponse,
    MatchedCreator,
    MatchedOpportunity,
    MatchOpportunitiesRequest,
    MatchOpportunitiesResponse,
)
from conectaai.schemas.creator import creator_to_response
from conectaai.schemas.mandate import DeliverableSpec
from conectaai.services import matching_service

router = APIRouter(prefix="/ai/match", tags=["IA — Matching semântico"])

_MAX_LIMIT = 30


@router.post("/creators", response_model=MatchCreatorsResponse)
def match_creators(
    data: MatchCreatorsRequest,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    text = data.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Descreva o que você procura")
    limit = max(1, min(data.limit, _MAX_LIMIT))

    creators = [c for c in CreatorRepository.get_all(db) if c.available]

    ranked = matching_service.rank_creators(db, text, creators, limit=limit)
    source = "semantic"
    if ranked is None:
        source = "heuristic"
        scored = [(c, matching_service.keyword_score_creator(text, c)) for c in creators]
        scored.sort(key=lambda x: x[1], reverse=True)
        ranked = scored[:limit]

    results: List[MatchedCreator] = []
    for creator, score in ranked:
        response = creator_to_response(creator)
        response.match_score = _to_pct(score, source)
        response.match_reason = (
            "Compatibilidade calculada por similaridade semântica com a sua busca."
            if source == "semantic"
            else "Compatibilidade estimada por nicho, cidade e engajamento (semântica indisponível no momento)."
        )
        results.append(MatchedCreator(creator=response, match_score=response.match_score, match_reason=response.match_reason))

    return MatchCreatorsResponse(text=text, results=results, total_found=len(creators), source=source)


@router.post("/opportunities", response_model=MatchOpportunitiesResponse)
def match_opportunities(
    data: MatchOpportunitiesRequest,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: Session = Depends(get_db),
):
    text = data.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Descreva o que você procura")
    limit = max(1, min(data.limit, _MAX_LIMIT))

    mandates = (
        db.query(CommercialMandateDB)
        .filter(CommercialMandateDB.owner_type == "company", CommercialMandateDB.active.is_(True), CommercialMandateDB.campaign_id.isnot(None))
        .all()
    )
    company_names = {}
    mandates_with_company = []
    for m in mandates:
        if m.owner_id not in company_names:
            company = CompanyRepository.get_by_id(db, m.owner_id)
            company_names[m.owner_id] = company.name if company else ""
        mandates_with_company.append((m, company_names[m.owner_id]))

    ranked = matching_service.rank_mandates(db, text, mandates_with_company, limit=limit)
    source = "semantic"
    if ranked is None:
        source = "heuristic"
        scored = [(m, name, matching_service.keyword_score_mandate(text, m, name)) for m, name in mandates_with_company]
        scored.sort(key=lambda x: x[2], reverse=True)
        ranked = scored[:limit]

    results: List[MatchedOpportunity] = []
    for mandate, company_name, score in ranked:
        pct = _to_pct(score, source)
        results.append(
            MatchedOpportunity(
                mandate_id=mandate.id,
                campaign_id=mandate.campaign_id,
                company_id=mandate.owner_id,
                company_name=company_name,
                objective=mandate.objective or "",
                ideal_price=mandate.ideal_price,
                price_ceiling=mandate.price_ceiling,
                deliverables=[DeliverableSpec(**d) for d in (mandate.deliverables or [])],
                match_score=pct,
                match_reason=(
                    "Compatibilidade calculada por similaridade semântica com a sua busca."
                    if source == "semantic"
                    else "Compatibilidade estimada por palavras-chave (semântica indisponível no momento)."
                ),
            )
        )

    return MatchOpportunitiesResponse(text=text, results=results, total_found=len(mandates_with_company), source=source)


def _to_pct(score: float, source: str) -> int:
    if source == "heuristic":
        return int(max(0, min(100, score)))
    # cosseno de vetores normalizados: [-1, 1], mas matches relevantes do
    # Gemini ficam quase sempre positivos — clampa e escala pra 0-100.
    return int(max(0, min(1, score)) * 100)
