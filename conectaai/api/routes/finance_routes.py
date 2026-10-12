# Arquivo: conectaai/api/routes/finance_routes.py
#
# Financeiro: a conta Stripe Connect do usuário (empresa ou creator). É por ela que ele paga e recebe na
# plataforma. O cadastro e o painel de saques são páginas da Stripe; aqui só criamos a conta, geramos os links e
# guardamos a situação. Ver services/payments/stripe_connect.py.
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from conectaai.core.config import settings
from conectaai.core.database import get_db
from conectaai.api.deps import self_id
from conectaai.core.security import CurrentUser, get_current_user, require_role
from conectaai.models.sql_models import PaymentAccountDB
from conectaai.repositories.payment_account_repo import PaymentAccountRepository
from conectaai.repositories.user_repo import UserRepository
from conectaai.repositories.payment_repo import PaymentRepository
from conectaai.schemas.finance import (
    OnboardingRequest,
    PayRequest,
    PaymentAccountResponse,
    PaymentResponse,
    RedirectResponse,
)
from conectaai.services import collaboration_service
from conectaai.services.payments import payment_service, stripe_connect

router = APIRouter(prefix="/finance", tags=["Financeiro"])
logger = logging.getLogger("conectaai.payments")

_UNAVAILABLE = "Os pagamentos ainda não estão disponíveis. Tente mais tarde."


def _response(account: PaymentAccountDB | None) -> PaymentAccountResponse:
    if account is None:
        return PaymentAccountResponse(available=stripe_connect.is_configured())
    if account.charges_enabled and account.payouts_enabled:
        status = "active"
    elif account.details_submitted:
        status = "restricted"  # cadastro enviado, mas a Stripe ainda pede algo (ou está analisando)
    else:
        status = "pending"
    return PaymentAccountResponse(
        available=stripe_connect.is_configured(),
        connected=True,
        status=status,
        details_submitted=bool(account.details_submitted),
        charges_enabled=bool(account.charges_enabled),
        payouts_enabled=bool(account.payouts_enabled),
        requirements_due=list(account.requirements_due or []),
        country=account.country,
    )


@router.get("/account", response_model=PaymentAccountResponse)
def get_account(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Situação da conta de pagamentos. Consulta a Stripe para vir atualizada (é o que a tela mostra ao voltar do
    cadastro); se a Stripe não responder, devolve o último estado guardado."""
    account = PaymentAccountRepository.get_by_user_id(db, current_user.user_id)
    if account is not None and stripe_connect.is_configured():
        try:
            account = PaymentAccountRepository.update_status(db, account, stripe_connect.account_status(account.stripe_account_id))
        except (stripe_connect.PaymentsError, stripe_connect.PaymentsUnavailable):
            pass
    return _response(account)


@router.post("/account/onboarding", response_model=RedirectResponse)
def start_onboarding(
    data: OnboardingRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cria a conta Stripe Connect (na primeira vez) e devolve o link do cadastro na Stripe. Serve também para
    continuar um cadastro incompleto ou enviar o que a Stripe pediu depois."""
    if not stripe_connect.is_configured():
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)
    return_url = stripe_connect.safe_app_url(data.return_url, "/#/finance?stripe=return")
    refresh_url = stripe_connect.safe_app_url(data.refresh_url, "/#/finance?stripe=refresh")
    if not return_url or not refresh_url:
        raise HTTPException(status_code=400, detail="Endereço de retorno inválido")
    try:
        account = PaymentAccountRepository.get_by_user_id(db, current_user.user_id)
        if account is None:
            user = UserRepository.get_by_id(db, current_user.user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="Usuário não encontrado")
            stripe_id = stripe_connect.create_account(
                email=user.email, role=current_user.role, user_id=user.id, country=settings.STRIPE_COUNTRY
            )
            account = PaymentAccountRepository.create(db, user.id, current_user.role, stripe_id, settings.STRIPE_COUNTRY)
        url = stripe_connect.create_onboarding_link(account.stripe_account_id, return_url=return_url, refresh_url=refresh_url)
    except stripe_connect.PaymentsUnavailable:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)
    except stripe_connect.PaymentsError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return RedirectResponse(url=url)


@router.post("/account/dashboard", response_model=RedirectResponse)
def open_dashboard(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Link para o painel da Stripe (saldo, saques, dados bancários). Só depois de concluir o cadastro."""
    if not stripe_connect.is_configured():
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)
    account = PaymentAccountRepository.get_by_user_id(db, current_user.user_id)
    if account is None or not account.details_submitted:
        raise HTTPException(status_code=409, detail="Conclua o cadastro da conta de pagamentos primeiro")
    try:
        return RedirectResponse(url=stripe_connect.create_dashboard_link(account.stripe_account_id))
    except stripe_connect.PaymentsUnavailable:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)
    except stripe_connect.PaymentsError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# --- Pagamento dos acordos -------------------------------------------------------


@router.get("/payments", response_model=list[PaymentResponse])
def list_payments(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Histórico: o que a empresa pagou, ou o que o creator recebeu (os mais novos primeiro)."""
    return payment_service.list_for(db, role=current_user.role, profile_id=self_id(db, current_user))


@router.post("/payments", response_model=RedirectResponse)
def pay_collaboration(
    data: PayRequest,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    """A empresa paga um acordo (proposta aceita dela): devolve a página de pagamento da Stripe. O valor fica
    reservado na plataforma até ela confirmar que o acordo foi cumprido (POST /finance/payments/{id}/release)."""
    success_url = stripe_connect.safe_app_url(data.success_url, f"/#/collaborations/{data.proposal_id}?payment=ok")
    cancel_url = stripe_connect.safe_app_url(data.cancel_url, f"/#/collaborations/{data.proposal_id}?payment=cancel")
    if not success_url or not cancel_url:
        raise HTTPException(status_code=400, detail="Endereço de retorno inválido")
    try:
        proposal = collaboration_service.get_for_participant(db, data.proposal_id, role="company", profile_id=self_id(db, current_user))
        url = payment_service.start(db, proposal, company_user_id=current_user.user_id, success_url=success_url, cancel_url=cancel_url)
    except collaboration_service.CollaborationError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail)
    except payment_service.PaymentRefused as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail)
    return RedirectResponse(url=url)


@router.post("/payments/{proposal_id}/release", response_model=PaymentResponse)
def release_payment(
    proposal_id: str,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    """A empresa confirma que o acordo foi cumprido: o valor reservado é repassado para a conta do creator."""
    try:
        proposal = collaboration_service.get_for_participant(db, proposal_id, role="company", profile_id=self_id(db, current_user))
        payment = payment_service.release(db, proposal)
    except collaboration_service.CollaborationError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail)
    except payment_service.PaymentRefused as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail)
    return payment_service.to_response(db, payment, role=current_user.role)


@router.post("/payments/{proposal_id}/sync", response_model=PaymentResponse)
def sync_payment(proposal_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Confere com a Stripe o pagamento de um acordo — o app chama ao voltar da página de pagamento, para não
    depender só do aviso da Stripe chegar antes."""
    try:
        collaboration_service.get_for_participant(db, proposal_id, role=current_user.role, profile_id=self_id(db, current_user))
    except collaboration_service.CollaborationError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail)
    payment = payment_service.sync(db, PaymentRepository.get_by_proposal(db, proposal_id))
    if payment is None:
        raise HTTPException(status_code=404, detail="Este acordo ainda não tem pagamento")
    return payment_service.to_response(db, payment, role=current_user.role)


@router.post("/stripe/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Avisos da Stripe (sem login: a autenticidade vem da assinatura Stripe-Signature).
    `account.updated` atualiza a situação da conta — é assim que "em análise" vira "ativa" sem o usuário abrir a
    tela; `checkout.session.*` confirma (ou encerra) o pagamento de um acordo."""
    payload = await request.body()
    try:
        event = stripe_connect.parse_webhook(payload, request.headers.get("Stripe-Signature"))
    except stripe_connect.PaymentsUnavailable:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)
    except ValueError:
        raise HTTPException(status_code=400, detail="Assinatura do aviso inválida")

    if event.get("type") == "account.updated":
        data = (event.get("data") or {}).get("object") or {}
        account = PaymentAccountRepository.get_by_stripe_id(db, data.get("id", ""))
        if account is not None:
            PaymentAccountRepository.update_status(db, account, stripe_connect.status_of(data))
    else:
        payment_service.handle_event(db, event)
    return {"received": True}
