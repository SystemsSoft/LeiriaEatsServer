# Arquivo: conectaai/services/match_search.py
#
# A busca bilateral em si (ver api/routes/match_routes.py), separada das rotas
# para ser reaproveitada pela conversa de voz (services/ai/live_voice.py): a
# IA por voz chama a MESMA busca do chat por texto, com o mesmo fallback por
# palavra-chave quando a semântica está indisponível.
from typing import List

from sqlalchemy.orm import Session

from conectaai.models.sql_models import CommercialMandateDB
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.schemas.ai import (
    MatchCreatorsResponse,
    MatchedCreator,
    MatchedOpportunity,
    MatchOpportunitiesResponse,
)
from conectaai.schemas.creator import creator_to_response
from conectaai.schemas.mandate import DeliverableSpec
from conectaai.services import matching_service

MAX_LIMIT = 30


def search_creators(db: Session, text: str, limit: int) -> MatchCreatorsResponse:
    limit = max(1, min(limit, MAX_LIMIT))
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


def search_opportunities(db: Session, text: str, limit: int) -> MatchOpportunitiesResponse:
    limit = max(1, min(limit, MAX_LIMIT))
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
