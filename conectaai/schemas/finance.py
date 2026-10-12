# Arquivo: conectaai/schemas/finance.py
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class PaymentAccountResponse(BaseModel):
    # False = o servidor ainda não tem a chave da Stripe: o app mostra "pagamentos ainda não disponíveis".
    available: bool
    # Já criou a conta Stripe Connect (mesmo sem terminar o cadastro).
    connected: bool = False
    # not_started | pending (cadastro incompleto) | restricted (a Stripe pede mais dados) | active
    status: str = "not_started"
    details_submitted: bool = False
    charges_enabled: bool = False  # pode receber
    payouts_enabled: bool = False  # pode sacar para o banco
    requirements_due: List[str] = []
    country: Optional[str] = None


class OnboardingRequest(BaseModel):
    # Para onde a Stripe devolve o usuário: ao terminar (return) e quando o link expira (refresh).
    return_url: Optional[str] = None
    refresh_url: Optional[str] = None


class RedirectResponse(BaseModel):
    url: str


class PayRequest(BaseModel):
    proposal_id: str
    # Para onde a Stripe devolve a empresa: depois de pagar (success) e se desistir (cancel).
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class PaymentResponse(BaseModel):
    id: str
    proposal_id: str
    campaign_name: str
    counterpart_name: str  # empresa vê o creator; creator vê a empresa
    amount: float  # valor do acordo
    fee: float  # taxa da plataforma
    net: float  # o que o creator recebe
    currency: str
    status: str  # pending | paid (reservado) | released (liberado ao creator) | failed | canceled
    created_at: Optional[datetime]
    paid_at: Optional[datetime]
    released_at: Optional[datetime] = None
