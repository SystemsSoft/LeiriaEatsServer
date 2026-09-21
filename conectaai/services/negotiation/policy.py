# Arquivo: conectaai/services/negotiation/policy.py
#
# Núcleo de segurança do fluxo de negociação: função pura, sem IO, sem
# chamada de rede, sem acesso a banco. Recebe o mandato do ator que está
# fazendo a proposta NESTE turno (nunca o mandato do outro lado — cada
# agente só é limitado pela própria autoridade) e os termos que o LLM (ou o
# agente determinístico) propôs, e devolve o que efetivamente vale depois de
# aplicar os limites, mais a lista do que foi cortado.
#
# Isso existe para que, mesmo se o LLM alucinar ou for manipulado por texto
# hostil da contraparte (prompt injection), nenhum valor fora do mandato
# chegue a ser persistido como oferta real.
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class PolicyResult:
    terms: Dict[str, Any]
    violations: List[str] = field(default_factory=list)

    @property
    def is_degenerate(self) -> bool:
        """Termos inutilizáveis: sem preço nenhum definido, ou 3+ violações
        no mesmo turno — nesses casos o motor de negociação (engine.py) deve
        abortar o turno em vez de seguir com uma oferta capenga."""
        return self.terms.get("price") is None or len(self.violations) >= 3


def _clamp(value: float, low: Optional[float], high: Optional[float]) -> float:
    if low is not None:
        value = max(value, low)
    if high is not None:
        value = min(value, high)
    return value


def apply(mandate: Dict[str, Any], previous_terms: Dict[str, Any], proposed_terms: Dict[str, Any], *, now: Optional[datetime] = None) -> PolicyResult:
    """`mandate` e `previous_terms`/`proposed_terms` são dicts simples (não
    objetos ORM/Pydantic) de propósito — mantém a função testável sem
    precisar de sessão de banco nem de um schema específico."""
    now = now or datetime.now(timezone.utc)
    negotiable = set(mandate.get("negotiable_fields") or [])
    terms: Dict[str, Any] = dict(previous_terms or {})
    violations: List[str] = []

    owner_type = mandate.get("owner_type")

    # --- preço ---
    if "price" in proposed_terms and proposed_terms["price"] is not None:
        if "price" not in negotiable and previous_terms.get("price") is not None:
            violations.append("price_not_negotiable")
        else:
            price = float(proposed_terms["price"])
            if owner_type == "company":
                clamped = _clamp(price, None, mandate.get("price_ceiling") or None)
            else:  # creator
                floor = mandate.get("price_floor") or None
                clamped = _clamp(price, floor, None)
            if clamped != price:
                violations.append("price_out_of_mandate")
            terms["price"] = clamped
    elif "price" not in terms:
        terms["price"] = mandate.get("ideal_price") or 0

    # --- entregáveis ---
    if "deliverables" in proposed_terms and proposed_terms["deliverables"] is not None:
        if "deliverables" not in negotiable and previous_terms.get("deliverables"):
            violations.append("deliverables_not_negotiable")
        else:
            catalog = {d["content_type"]: d for d in (mandate.get("deliverables") or [])}
            cleaned = []
            for item in proposed_terms["deliverables"]:
                spec = catalog.get(item["content_type"])
                if spec is None:
                    violations.append(f"deliverable_not_in_catalog:{item['content_type']}")
                    continue
                qty = _clamp(item["quantity"], spec.get("min_qty"), spec.get("max_qty"))
                if qty != item["quantity"]:
                    violations.append(f"deliverable_qty_out_of_mandate:{item['content_type']}")
                cleaned.append({"content_type": item["content_type"], "quantity": int(qty)})
            if cleaned:
                terms["deliverables"] = cleaned
            elif not previous_terms.get("deliverables"):
                violations.append("no_valid_deliverables")

    # --- prazo ---
    if "deadline_days" in proposed_terms and proposed_terms["deadline_days"] is not None:
        if "deadline" not in negotiable and previous_terms.get("deadline_days") is not None:
            violations.append("deadline_not_negotiable")
        else:
            days = int(proposed_terms["deadline_days"])
            earliest = mandate.get("deadline_earliest")
            latest = mandate.get("deadline_latest")
            min_days = max(1, (earliest - now).days) if earliest else None
            max_days = max(1, (latest - now).days) if latest else None
            clamped_days = int(_clamp(days, min_days, max_days))
            if clamped_days != days:
                violations.append("deadline_out_of_mandate")
            terms["deadline_days"] = clamped_days

    # --- exclusividade ---
    if "exclusivity" in proposed_terms and proposed_terms["exclusivity"] is not None:
        wants_exclusivity = bool(proposed_terms["exclusivity"])
        if wants_exclusivity and not mandate.get("exclusivity_allowed", False):
            violations.append("exclusivity_not_allowed")
            terms["exclusivity"] = False
        elif "exclusivity" not in negotiable and previous_terms.get("exclusivity") is not None and wants_exclusivity != previous_terms.get("exclusivity"):
            violations.append("exclusivity_not_negotiable")
        else:
            terms["exclusivity"] = wants_exclusivity
    elif "exclusivity" not in terms:
        terms["exclusivity"] = False

    return PolicyResult(terms=terms, violations=violations)


def counterparty_gap_is_unbridgeable(company_mandate: Dict[str, Any], creator_mandate: Dict[str, Any]) -> bool:
    """Checagem de impasse ANTES de qualquer chamada ao LLM — custo zero. Se
    o teto da empresa já é menor que o piso do creator, nenhuma rodada de
    conversa muda isso; não vale a pena gastar uma chamada de IA (e o tempo
    de p99 de dezenas de segundos) para descobrir o óbvio."""
    ceiling = company_mandate.get("price_ceiling") or 0
    floor = creator_mandate.get("price_floor") or 0
    return ceiling > 0 and floor > 0 and ceiling < floor
