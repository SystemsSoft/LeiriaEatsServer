# Arquivo: conectaai/schemas/ai_structured.py
#
# Contratos de saída do Gemini (JSON mode, via response_schema em
# gemini_client.generate_json). `extra="forbid"` recusa qualquer campo que o
# modelo tenha inventado — se a resposta não bater exatamente com o schema,
# a chamada falha e cai no fallback determinístico, nunca é aceita "quase certa".
#
# IMPORTANTE: nenhum desses modelos é gravado direto no banco. Toda instância
# passa por services/negotiation/policy.py antes de virar `terms_after_policy`
# — o LLM só propõe, o código decide o que vale.
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ProposedDeliverable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: str
    quantity: int = Field(ge=0, le=100)


class ProposedTerms(BaseModel):
    """Termos que o agente propõe neste turno. Todos opcionais — o modelo só
    preenche o que está mudando; o que fica None mantém o valor anterior."""

    model_config = ConfigDict(extra="forbid")

    price: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    deliverables: Optional[List[ProposedDeliverable]] = None
    deadline_days: Optional[int] = Field(default=None, ge=1, le=365)
    exclusivity: Optional[bool] = None


class NegotiationTurnOutput(BaseModel):
    """Saída de 1 turno do agente. `message_template` usa placeholders
    ({price}, {deadline_days}, {deliverables}) — o texto final é montado em
    Python a partir de `terms_after_policy`, nunca do valor que o LLM
    escreveu livre no texto."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["offer", "counter_offer", "accept", "reject"]
    proposed_terms: ProposedTerms
    message_template: str = Field(max_length=400)
    rationale: str = Field(max_length=300, default="")


class ExtractedTerms(BaseModel):
    """Extração dos termos finais a partir do histórico da negociação, usada
    para montar o Agreement quando `is_final_agreement=True`."""

    model_config = ConfigDict(extra="forbid")

    price: float = Field(ge=0, le=1_000_000)
    deliverables: List[ProposedDeliverable] = Field(default_factory=list)
    deadline_days: int = Field(ge=1, le=365)
    exclusivity: bool = False
    is_final_agreement: bool
    confidence: float = Field(ge=0, le=1)


class CampaignMandateDraft(BaseModel):
    """Preview gerado a partir do briefing em texto livre da empresa — não
    persiste nada sozinho; só populam o formulário de campanha + mandato que
    o humano confirma em /mandates."""

    model_config = ConfigDict(extra="forbid")

    campaign_name: str = Field(max_length=255)
    objective: str = Field(max_length=255)
    target_count: int = Field(ge=1, le=1000)
    desired_categories: List[str] = Field(default_factory=list)
    city: str = ""
    budget_total: float = Field(ge=0, le=10_000_000)
    ideal_price: float = Field(ge=0, le=1_000_000)
    price_ceiling: float = Field(ge=0, le=1_000_000)
    deliverables: List[ProposedDeliverable] = Field(default_factory=list)
    clarifying_question: str = Field(default="", max_length=300)
