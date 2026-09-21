# Arquivo: conectaai/services/negotiation/fallback_agent.py
#
# Agente 100% determinístico, sem LLM — usado quando não há GEMINI_API_KEYS
# configurada (config.py) ou quando o Gemini falha em todas as tentativas
# (engine.py trata os dois casos do mesmo jeito). Garante que "dois agentes
# negociam sozinhos" continue funcionando de ponta a ponta mesmo sem chave de
# API configurada — é o que a fase 2 do plano de implementação exige.
#
# Estratégia: cada lado ancora no próprio valor ideal e, a cada rodada,
# caminha até a metade do caminho até a última oferta conhecida da
# contraparte, sempre dentro do próprio mandato. Aceita quando a distância
# fica pequena o suficiente para não valer mais uma rodada.
from typing import Any, Dict, Optional

_ACCEPT_THRESHOLD_RATIO = 0.02  # aceita quando a diferença é <= 2% do preço, ou <= R$ 1
_ACCEPT_THRESHOLD_MIN = 1.0


def _initial_price(mandate: Dict[str, Any]) -> float:
    if mandate.get("ideal_price"):
        return float(mandate["ideal_price"])
    if mandate.get("owner_type") == "company":
        return float(mandate.get("price_ceiling") or 0) * 0.8
    return float(mandate.get("price_floor") or 0) * 1.2 if mandate.get("price_floor") else 0.0


def propose_turn(
    *, mandate: Dict[str, Any], side: str, my_last_price: Optional[float], other_last_price: Optional[float]
) -> Dict[str, Any]:
    """Devolve um dict no formato de NegotiationTurnOutput (intent,
    proposed_terms, message_template, rationale) sem passar por Pydantic —
    quem chama (engine.py) já sabe que veio do fallback e aplica policy.apply
    normalmente, do mesmo jeito que faria com a saída do Gemini."""
    deliverables = mandate.get("deliverables") or []
    proposed_deliverables = [{"content_type": d["content_type"], "quantity": d.get("min_qty", 1)} for d in deliverables] or None

    if other_last_price is None:
        price = _initial_price(mandate)
        return {
            "intent": "offer",
            "proposed_terms": {"price": round(price, 2), "deliverables": proposed_deliverables, "deadline_days": None, "exclusivity": None},
            "message_template": "Proposta: {price}" + (f" — {{deliverables}}" if proposed_deliverables else ""),
            "rationale": "Oferta inicial ancorada no valor ideal do mandato (agente determinístico).",
        }

    anchor = my_last_price if my_last_price is not None else _initial_price(mandate)
    midpoint = (anchor + other_last_price) / 2

    gap = abs(midpoint - other_last_price)
    threshold = max(_ACCEPT_THRESHOLD_MIN, midpoint * _ACCEPT_THRESHOLD_RATIO)
    if gap <= threshold:
        return {
            "intent": "accept",
            "proposed_terms": {"price": round(other_last_price, 2)},
            "message_template": "Aceito {price}.",
            "rationale": "Diferença dentro da margem de convergência (agente determinístico).",
        }

    return {
        "intent": "counter_offer",
        "proposed_terms": {"price": round(midpoint, 2)},
        "message_template": "Posso fazer por {price}.",
        "rationale": "Convergindo para o meio do caminho, dentro do próprio mandato (agente determinístico).",
    }
