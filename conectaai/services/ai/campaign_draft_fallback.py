# Arquivo: conectaai/services/ai/campaign_draft_fallback.py
#
# Extração determinística por regex, usada quando não há GEMINI_API_KEYS
# configurada ou a chamada falha — mesmo princípio do
# services/negotiation/fallback_agent.py: nunca deixa o usuário sem
# resposta, mesmo que o resultado seja mais simples. Aqui o risco é baixo
# (é só um pré-preenchimento de formulário, não uma decisão de negócio),
# então o fallback é propositalmente simples.
import re

from conectaai.schemas.ai import CampaignDraftResponse
from conectaai.schemas.mandate import DeliverableSpec

_COUNT_PATTERN = re.compile(r"(\d+)\s*(creators?|criador(?:es)?|influenciador(?:es)?)", re.IGNORECASE)
_BUDGET_PATTERN = re.compile(r"R\$\s*([\d.,]+)", re.IGNORECASE)


def heuristic_draft(text: str) -> CampaignDraftResponse:
    count_match = _COUNT_PATTERN.search(text)
    target_count = int(count_match.group(1)) if count_match else 1

    budget_match = _BUDGET_PATTERN.search(text)
    budget_total = 0.0
    if budget_match:
        raw = budget_match.group(1).replace(".", "").replace(",", ".")
        try:
            budget_total = float(raw)
        except ValueError:
            budget_total = 0.0

    ideal_price = round(budget_total / target_count, 2) if budget_total and target_count else 0.0
    price_ceiling = round(ideal_price * 1.3, 2) if ideal_price else 0.0

    missing = []
    if not budget_match:
        missing.append("o orçamento")
    if not count_match:
        missing.append("quantos creators você precisa")
    clarifying = (
        f"Não consegui identificar {' e '.join(missing)} automaticamente — revise os campos abaixo."
        if missing
        else "Revise os detalhes abaixo antes de criar a campanha."
    )

    return CampaignDraftResponse(
        campaign_name=(text.strip()[:60] or "Nova campanha"),
        objective=text.strip()[:255],
        target_count=target_count,
        desired_categories=[],
        city="",
        budget_total=budget_total,
        ideal_price=ideal_price,
        price_ceiling=price_ceiling,
        deliverables=[DeliverableSpec(content_type="Reel", min_qty=1, max_qty=max(1, target_count))],
        clarifying_question=clarifying,
        source="heuristic",
    )
