# Arquivo: conectaai/services/ai_service.py
#
# v1: porta 1:1 a lógica de keyword-matching que já existe no app Flutter
# (lib/data/repositories/ai_repository.dart e creator_ai_repository.dart),
# agora operando sobre linhas reais do banco em vez de fixtures mockados.
# matchScore/matchBreakdown não são colunas persistidas — são calculados aqui,
# em tempo de request, porque dependem de quem está buscando.
from datetime import datetime
from typing import List, Optional, Tuple

from conectaai.models.sql_models import CreatorDB, OpportunityDB

_FAR_FUTURE = datetime.max

COMPANY_DEFAULT_QUICK_REPLIES = [
    "Somente mulheres",
    "Priorize maior engajamento",
    "Somente Instagram",
    "Mostre microinfluenciadores",
]

CREATOR_DEFAULT_QUICK_REPLIES = [
    "Prazo mais curto",
    "Somente Reels",
    "Mais compatíveis com meu perfil",
    "Categoria Beleza",
]


def compute_creator_match(creator: CreatorDB, desired_categories: List[str]) -> Tuple[int, dict, str]:
    """Pontuação simples e determinística (sem ML) — cada critério soma até um teto."""
    niche = 40 if any(c in (creator.categories or []) for c in desired_categories) else 15
    audience = min(30, int((creator.engagement_rate or 0) * 6))
    location = 15
    engagement = min(20, int((creator.engagement_rate or 0) * 4))
    price = 10
    score = min(100, niche + audience + location + engagement + price)
    breakdown = {
        "niche": niche,
        "audience": audience,
        "location": location,
        "engagement": engagement,
        "price": price,
    }
    reason = f"Compatibilidade calculada a partir de nicho, engajamento ({creator.engagement_rate}%) e plataformas."
    return score, breakdown, reason


def compute_opportunity_match(opportunity: OpportunityDB, creator: CreatorDB) -> int:
    score = 50
    if opportunity.category and any(
        opportunity.category.lower() in c.lower() or c.lower() in opportunity.category.lower()
        for c in (creator.categories or [])
    ):
        score += 25
    if creator.price_min and creator.price_max:
        mid_budget = ((opportunity.budget_min or 0) + (opportunity.budget_max or 0)) / 2
        if creator.price_min <= mid_budget <= creator.price_max:
            score += 25
    return min(100, score)


def company_chat(text: str, creators: List[CreatorDB], desired_categories: Optional[List[str]] = None):
    lower = text.lower()
    desired_categories = desired_categories or []
    pool = list(creators)

    def scored(items: List[CreatorDB]):
        return sorted(items, key=lambda c: compute_creator_match(c, desired_categories)[0], reverse=True)

    if "mulher" in lower:
        filtered = [c for c in pool if (c.audience_info or {}).get("female_percent", 0) >= 50]
        return "Filtrei creators com audiência majoritariamente feminina.", scored(filtered)[:5], len(filtered)

    if "engajamento" in lower:
        sorted_pool = sorted(pool, key=lambda c: c.engagement_rate or 0, reverse=True)
        return "Priorizei os creators com maior taxa de engajamento.", sorted_pool[:5], len(sorted_pool)

    if "instagram" in lower:
        filtered = [c for c in pool if "Instagram" in (c.platforms or [])]
        return "Mostrando apenas creators com presença no Instagram.", scored(filtered)[:5], len(filtered)

    if "microinfluenc" in lower:
        filtered = [c for c in pool if (c.followers or 0) <= 100_000]
        return "Aqui estão os microinfluenciadores mais compatíveis.", scored(filtered)[:5], len(filtered)

    matched = [c for c in pool if compute_creator_match(c, desired_categories)[0] >= 40]
    matched = scored(matched)
    text_reply = (
        f"Entendi. Vou procurar creators com forte compatibilidade de nicho e audiência.\n\n"
        f"Encontrei {len(matched)} creators. Separei os {min(5, len(matched))} mais fortes."
    )
    return text_reply, matched[:5], len(matched)


def creator_chat(text: str, opportunities: List[OpportunityDB], creator: CreatorDB):
    lower = text.lower()
    pool = list(opportunities)

    def scored(items: List[OpportunityDB]):
        return sorted(items, key=lambda o: compute_opportunity_match(o, creator), reverse=True)

    if any(k in lower for k in ("prazo", "urgente", "rápid", "rapid")):
        sorted_pool = sorted(pool, key=lambda o: o.deadline or _FAR_FUTURE)
        return "Priorizei as campanhas com prazo mais próximo.", sorted_pool[:5], len(sorted_pool)

    if "reel" in lower:
        filtered = [o for o in pool if "reel" in (o.content_type or "").lower()]
        return "Mostrando apenas campanhas que pedem Reels.", scored(filtered)[:5], len(filtered)

    if "beleza" in lower or "beauty" in lower:
        filtered = [o for o in pool if "beauty" in (o.category or "").lower() or "beleza" in (o.category or "").lower()]
        return "Aqui estão as campanhas de Beleza disponíveis.", scored(filtered)[:5], len(filtered)

    if any(k in lower for k in ("compat", "perfil", "match")):
        sorted_pool = scored(pool)
        return "Reordenei priorizando a maior compatibilidade com o seu perfil.", sorted_pool[:5], len(sorted_pool)

    matched = [o for o in pool if compute_opportunity_match(o, creator) >= 40]
    matched = scored(matched)
    text_reply = (
        f"Entendi. Vou procurar campanhas com forte compatibilidade de categoria e formato de "
        f"conteúdo com o seu perfil.\n\nEncontrei {len(matched)} oportunidades. Separei as "
        f"{min(5, len(matched))} mais compatíveis."
    )
    return text_reply, matched[:5], len(matched)
