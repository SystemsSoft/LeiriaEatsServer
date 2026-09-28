# Arquivo: conectaai/schemas/ai_structured.py
#
# Contrato de saída do Gemini (JSON mode, via response_schema em
# gemini_client.generate_json) usado no rascunho de campanha por IA — a
# única chamada de GERAÇÃO que sobra no módulo (a negociação em si é 100%
# humana, ver services/negotiation/human.py; a IA aqui só faz busca
# semântica e este rascunho).
#
# NÃO usar `model_config = ConfigDict(extra="forbid")`: isso faz o
# `model_json_schema()` do Pydantic emitir `"additionalProperties": false`,
# e a API do Gemini rejeita esse campo em response_schema com 400
# INVALID_ARGUMENT ("Unknown name additional_properties") — o dialeto de
# schema que ela aceita é um subconjunto restrito do OpenAPI 3.0.
#
# IMPORTANTE: este modelo não é gravado direto no banco — é só o preview que
# o humano revisa e edita antes de confirmar em POST /ai/campaigns/confirm.
from typing import List

from pydantic import BaseModel, Field


class ProposedDeliverable(BaseModel):
    content_type: str
    quantity: int = Field(ge=0, le=100)


class CampaignMandateDraft(BaseModel):
    """Preview gerado a partir do briefing em texto livre da empresa — não
    persiste nada sozinho; só popula o formulário de campanha que o humano
    confirma em /ai/campaigns/confirm."""

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
