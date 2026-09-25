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
        no mesmo turno, ou nenhum entregável do catálogo próprio sobrou — nesses
        casos o motor de negociação (engine.py) deve abortar o turno em vez de
        seguir com uma oferta capenga."""
        return (
            self.terms.get("price") is None
            or len(self.violations) >= 3
            or "no_valid_deliverables" in self.violations
        )


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

    # --- catálogo próprio ---
    # Nada que ESTE agente publique pode conter entregável fora do próprio
    # catálogo — inclusive o que veio herdado da oferta anterior da contraparte
    # (o bloco acima só filtra o que o agente PROPÔS neste turno).
    catalog = _catalog(mandate)
    if catalog and terms.get("deliverables"):
        kept = []
        for item in terms["deliverables"]:
            spec = catalog.get(item["content_type"])
            if spec is None:
                _flag(violations, f"deliverable_not_in_catalog:{item['content_type']}")
                continue
            qty = int(_clamp(item["quantity"], spec.get("min_qty"), spec.get("max_qty")))
            if qty != item["quantity"]:
                _flag(violations, f"deliverable_qty_out_of_mandate:{item['content_type']}")
            kept.append({"content_type": item["content_type"], "quantity": qty})
        if kept:
            terms["deliverables"] = kept
        else:
            terms.pop("deliverables", None)
            _flag(violations, "no_valid_deliverables")

    return PolicyResult(terms=terms, violations=violations)


def _catalog(mandate: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {d["content_type"]: d for d in (mandate.get("deliverables") or [])}


def _flag(violations: List[str], code: str) -> None:
    if code not in violations:
        violations.append(code)


def _deadline_bounds(mandate: Dict[str, Any], now: datetime):
    earliest = mandate.get("deadline_earliest")
    latest = mandate.get("deadline_latest")
    min_days = max(1, (earliest - now).days) if earliest else None
    max_days = max(1, (latest - now).days) if latest else None
    return min_days, max_days


def acceptance_violations(mandate: Dict[str, Any], offer: Dict[str, Any], *, now: Optional[datetime] = None) -> List[str]:
    """Por que `offer` (a oferta corrente, feita pela contraparte) NÃO cabe no
    mandato de quem quer aceitá-la — lista vazia = pode aceitar como está.

    `apply` só valida o que o agente PROPÕE; um `accept` propõe (quase) nada,
    então sem esta checagem um agente aceitaria entregáveis, quantidades,
    exclusividade ou prazo fora do próprio mandato — e o preço, no máximo,
    seria reescrito em silêncio para o limite, virando um acordo que a
    contraparte nunca ofereceu."""
    now = now or datetime.now(timezone.utc)
    found: List[str] = []

    price = offer.get("price")
    if price is None:
        return ["no_offer_to_accept"]
    if mandate.get("owner_type") == "company":
        ceiling = mandate.get("price_ceiling") or None
        if ceiling is not None and price > ceiling:
            found.append("price_out_of_mandate")
    else:
        floor = mandate.get("price_floor") or None
        if floor is not None and price < floor:
            found.append("price_out_of_mandate")

    catalog = _catalog(mandate)
    if catalog:
        for item in offer.get("deliverables") or []:
            spec = catalog.get(item["content_type"])
            if spec is None:
                found.append(f"deliverable_not_in_catalog:{item['content_type']}")
            elif not (spec.get("min_qty", 1) <= item["quantity"] <= spec.get("max_qty", item["quantity"])):
                found.append(f"deliverable_qty_out_of_mandate:{item['content_type']}")

    if offer.get("exclusivity") and not mandate.get("exclusivity_allowed", False):
        found.append("exclusivity_not_allowed")

    days = offer.get("deadline_days")
    if days is not None:
        min_days, max_days = _deadline_bounds(mandate, now)
        if (min_days is not None and days < min_days) or (max_days is not None and days > max_days):
            found.append("deadline_out_of_mandate")

    return found


def conform_offer(mandate: Dict[str, Any], offer: Dict[str, Any]) -> Dict[str, Any]:
    """`offer` reduzida ao que o mandato permite — usada como contraproposta
    quando `acceptance_violations` impede o accept. Devolve termos no formato
    de `proposed_terms` (passa por `apply` em seguida, como qualquer proposta)."""
    proposed: Dict[str, Any] = {}

    price = offer.get("price")
    if price is not None:
        if mandate.get("owner_type") == "company":
            proposed["price"] = _clamp(float(price), None, mandate.get("price_ceiling") or None)
        else:
            proposed["price"] = _clamp(float(price), mandate.get("price_floor") or None, None)

    catalog = _catalog(mandate)
    kept = []
    for item in offer.get("deliverables") or []:
        spec = catalog.get(item["content_type"]) if catalog else {"min_qty": None, "max_qty": None}
        if spec is None:
            continue
        qty = int(_clamp(item["quantity"], spec.get("min_qty"), spec.get("max_qty")))
        kept.append({"content_type": item["content_type"], "quantity": qty})
    if kept:
        proposed["deliverables"] = kept

    if offer.get("deadline_days") is not None:
        proposed["deadline_days"] = offer["deadline_days"]
    if offer.get("exclusivity") is not None:
        proposed["exclusivity"] = bool(offer["exclusivity"]) and bool(mandate.get("exclusivity_allowed", False))

    return proposed


def deliverable_catalogs_are_disjoint(company_mandate: Dict[str, Any], creator_mandate: Dict[str, Any]) -> bool:
    """Nenhum tipo de entregável em comum (ou com faixas de quantidade que não
    se tocam) — não existe acordo possível, então dá para declarar impasse
    antes de gastar qualquer chamada de IA. Catálogo vazio de um dos lados =
    sem restrição, não é incompatibilidade."""
    company_catalog = _catalog(company_mandate)
    creator_catalog = _catalog(creator_mandate)
    if not company_catalog or not creator_catalog:
        return False
    for content_type, c in company_catalog.items():
        r = creator_catalog.get(content_type)
        if r is None:
            continue
        low = max(c.get("min_qty", 1), r.get("min_qty", 1))
        high = min(c.get("max_qty", low), r.get("max_qty", low))
        if low <= high:
            return False
    return True


def counterparty_gap_is_unbridgeable(company_mandate: Dict[str, Any], creator_mandate: Dict[str, Any]) -> bool:
    """Checagem de impasse ANTES de qualquer chamada ao LLM — custo zero. Se
    o teto da empresa já é menor que o piso do creator, nenhuma rodada de
    conversa muda isso; não vale a pena gastar uma chamada de IA (e o tempo
    de p99 de dezenas de segundos) para descobrir o óbvio."""
    ceiling = company_mandate.get("price_ceiling") or 0
    floor = creator_mandate.get("price_floor") or 0
    return ceiling > 0 and floor > 0 and ceiling < floor
