# Arquivo: conectaai/services/match_search.py
#
# A busca bilateral em si (ver api/routes/match_routes.py), separada das rotas.
#
# Busca de creators em duas etapas: (1) a busca semântica pré-seleciona os
# candidatos pelo texto do perfil (ou a palavra-chave, se a semântica cair);
# (2) o Gemini avalia esses candidatos com os dados do perfil — seguidores,
# preço, engajamento, público — e devolve nota e motivo por creator
# (services/ai/creator_rerank.py). Se a etapa 2 falhar, vale o ranking da 1.
from typing import List, Optional

from sqlalchemy.orm import Session

from conectaai.core.config import settings
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
from conectaai.services.ai import creator_rerank

MAX_LIMIT = 30


MAX_PREVIOUS_QUERIES = 4


def search_creators(db: Session, text: str, limit: int, previous_queries: Optional[List[str]] = None) -> MatchCreatorsResponse:
    limit = max(1, min(limit, MAX_LIMIT))
    previous_queries = [q for q in (previous_queries or []) if q and q.strip()][-MAX_PREVIOUS_QUERIES:]
    creators = [c for c in CreatorRepository.get_all(db) if c.available]

    # Etapa 1: pré-seleção. Com refinamento ("agora com mais seguidores"), o
    # texto sozinho não diz nada do nicho — os pedidos anteriores entram junto.
    query_text = " | ".join(previous_queries + [text])
    pool_size = max(limit, settings.MATCH_RERANK_CANDIDATES)
    ranked = matching_service.rank_creators(db, query_text, creators, limit=pool_size)
    source = "semantic"
    if ranked is None:
        source = "heuristic"
        scored = [(c, matching_service.keyword_score_creator(query_text, c)) for c in creators]
        scored.sort(key=lambda x: x[1], reverse=True)
        ranked = scored[:pool_size]

    # Etapa 2: a IA avalia os candidatos com os dados do perfil.
    reranked = creator_rerank.rerank(db, text, previous_queries, [c for c, _ in ranked], max_results=limit)
    if reranked is not None:
        summary, items = reranked
        results = []
        for creator, score, reason in items:
            response = creator_to_response(creator)
            response.match_score = score
            response.match_reason = reason
            results.append(MatchedCreator(creator=response, match_score=score, match_reason=reason))
        return MatchCreatorsResponse(text=text, results=results, total_found=len(results), source="ai", summary=summary)

    ranked = ranked[:limit]
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
