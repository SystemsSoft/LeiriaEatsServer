# Arquivo: conectaai/services/payments/payment_service.py
#
# Pagamento de um acordo (proposta aceita), com retenção — como nas plataformas de freelancers:
#   1. a empresa paga na página da Stripe (cartão etc.); o valor entra na conta da plataforma e fica RESERVADO
#      (status "paid"). A confirmação vem pelo aviso da Stripe (checkout.session.completed) e, por garantia, o app
#      pede uma conferência ao voltar da página de pagamento (sync);
#   2. quando a empresa confirma que o acordo foi cumprido, o servidor repassa o valor para a conta conectada do
#      creator, menos a taxa da plataforma (CONECTAAI_PLATFORM_FEE_PERCENT) — status "released".
# O creator vê que o dinheiro já está garantido antes de produzir; a empresa só libera depois de receber.
# O servidor nunca vê dados de cartão.
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from conectaai.core.config import settings
from conectaai.models.sql_models import NotificationDB, PaymentDB, ProposalDB
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.repositories.notification_repo import NotificationRepository
from conectaai.repositories.payment_account_repo import PaymentAccountRepository
from conectaai.repositories.payment_repo import PaymentRepository
from conectaai.repositories.user_repo import UserRepository
from conectaai.schemas.finance import PaymentResponse
from conectaai.services.payments import stripe_connect

MIN_AMOUNT_CENTS = 100  # abaixo disso a Stripe recusa a cobrança


class PaymentRefused(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def money(cents: int) -> str:
    """12345 -> '123,45 €' (euro, com os separadores usados em Portugal)."""
    text = f"{cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} €"


def fee_for(amount_cents: int) -> int:
    percent = max(0.0, min(settings.PLATFORM_FEE_PERCENT, 50.0))
    return int(round(amount_cents * percent / 100))


def _notify_once(db: Session, *, user_id: str, title: str, message: str) -> None:
    """Não repete o mesmo aviso (a empresa pode tentar pagar várias vezes antes de o creator cadastrar a conta)."""
    exists = db.query(NotificationDB).filter(
        NotificationDB.user_id == user_id, NotificationDB.title == title, NotificationDB.message == message,
    ).first()
    if not exists:
        NotificationRepository.create(db, user_id=user_id, type_="campaignUpdate", title=title, message=message)


def start(db: Session, proposal: ProposalDB, *, company_user_id: str, success_url: str, cancel_url: str) -> str:
    """A empresa dona do acordo [proposal] vai pagar: devolve o endereço da página de pagamento da Stripe."""
    if not stripe_connect.is_configured():
        raise PaymentRefused(503, "Os pagamentos ainda não estão disponíveis. Tente mais tarde.")
    amount = int(round(float(proposal.budget or 0) * 100))
    if amount < MIN_AMOUNT_CENTS:
        raise PaymentRefused(409, "Este acordo não tem um valor definido para pagar.")
    payment = PaymentRepository.get_by_proposal(db, proposal.id)
    if payment is not None and payment.status in ("paid", "released"):
        raise PaymentRefused(409, "Este acordo já foi pago.")

    company = CompanyRepository.get_by_id(db, proposal.company_id)
    creator = CreatorRepository.get_by_id(db, proposal.creator_id)
    if company is None or creator is None:
        raise PaymentRefused(404, "Acordo não encontrado")
    campaign = proposal.campaign_name or "campanha"

    # O creator não precisa ter a conta pronta para a empresa pagar (o valor fica reservado); precisa para receber.
    fee = fee_for(amount)
    user = UserRepository.get_by_id(db, company_user_id)
    try:
        session = stripe_connect.create_payment_checkout(
            amount_cents=amount, currency=settings.STRIPE_CURRENCY,
            description=f"Acordo com {creator.name} — {campaign}"[:120], transfer_group=transfer_group_of(proposal.id),
            success_url=success_url, cancel_url=cancel_url, customer_email=user.email if user else "",
            metadata={"conectaai_proposal_id": proposal.id},
        )
    except stripe_connect.PaymentsUnavailable:
        raise PaymentRefused(503, "Os pagamentos ainda não estão disponíveis. Tente mais tarde.")
    except stripe_connect.PaymentsError as exc:
        raise PaymentRefused(502, str(exc))

    if payment is None:
        payment = PaymentDB(proposal_id=proposal.id, company_id=proposal.company_id, creator_id=proposal.creator_id)
        db.add(payment)
    payment.amount_cents = amount
    payment.fee_cents = fee
    payment.currency = settings.STRIPE_CURRENCY
    payment.status = "pending"
    payment.checkout_session_id = session["id"]
    db.commit()
    return session["url"]


def transfer_group_of(proposal_id: str) -> str:
    return f"conectaai_proposal_{proposal_id}"


def _creator_account_ready(db: Session, creator) -> Optional[object]:
    account = PaymentAccountRepository.get_by_user_id(db, creator.user_id)
    return account if account is not None and account.charges_enabled else None


def mark_paid(db: Session, payment: PaymentDB, payment_intent: str = "") -> PaymentDB:
    if payment.status in ("paid", "released"):
        return payment  # aviso repetido da Stripe
    payment.status = "paid"
    payment.paid_at = _now()
    payment.payment_intent_id = payment_intent or payment.payment_intent_id or ""
    db.commit()

    proposal = db.query(ProposalDB).filter(ProposalDB.id == payment.proposal_id).first()
    company = CompanyRepository.get_by_id(db, payment.company_id)
    creator = CreatorRepository.get_by_id(db, payment.creator_id)
    campaign = (proposal.campaign_name if proposal else "") or "campanha"
    if creator is not None:
        net = payment.amount_cents - (payment.fee_cents or 0)
        ready = _creator_account_ready(db, creator) is not None
        NotificationRepository.create(
            db, user_id=creator.user_id, type_="campaignUpdate", title="Pagamento reservado",
            message=(
                f"{company.name if company else 'A empresa'} pagou o acordo de \"{campaign}\": {money(net)} estão reservados para você "
                "e serão liberados quando a empresa confirmar que o acordo foi cumprido."
                + ("" if ready else " Cadastre sua conta de pagamentos no Financeiro para poder receber.")
            ),
        )
    if company is not None:
        NotificationRepository.create(
            db, user_id=company.user_id, type_="campaignUpdate", title="Pagamento confirmado",
            message=(
                f"Seu pagamento de {money(payment.amount_cents)} (\"{campaign}\") foi confirmado e está reservado. "
                f"Quando {creator.name if creator else 'o creator'} cumprir o acordo, confirme para liberar o valor."
            ),
        )
    return payment


def release(db: Session, proposal: ProposalDB) -> PaymentDB:
    """A empresa confirmou que o acordo [proposal] foi cumprido: repassa o valor reservado para o creator."""
    if not stripe_connect.is_configured():
        raise PaymentRefused(503, "Os pagamentos ainda não estão disponíveis. Tente mais tarde.")
    payment = PaymentRepository.get_by_proposal(db, proposal.id)
    if payment is None or payment.status in ("pending", "failed", "canceled"):
        raise PaymentRefused(409, "Este acordo ainda não tem um pagamento confirmado para liberar.")
    if payment.status == "released":
        return payment  # já liberado (clique duplo, segunda aba)

    company = CompanyRepository.get_by_id(db, payment.company_id)
    creator = CreatorRepository.get_by_id(db, payment.creator_id)
    if creator is None:
        raise PaymentRefused(404, "Acordo não encontrado")
    campaign = proposal.campaign_name or "campanha"
    account = _creator_account_ready(db, creator)
    if account is None:
        _notify_once(
            db, user_id=creator.user_id, title="Cadastre sua conta para receber",
            message=f"{company.name if company else 'A empresa'} quer liberar o pagamento do acordo de \"{campaign}\". Abra o Financeiro e cadastre sua conta de pagamentos para receber.",
        )
        raise PaymentRefused(409, f"{creator.name} ainda não tem a conta de pagamentos ativa para receber. Já avisamos; o valor continua reservado.")

    net = payment.amount_cents - (payment.fee_cents or 0)
    try:
        transfer_id = stripe_connect.release_to_creator(
            amount_cents=net, currency=payment.currency or settings.STRIPE_CURRENCY, destination=account.stripe_account_id,
            payment_intent_id=payment.payment_intent_id, transfer_group=transfer_group_of(proposal.id),
            idempotency_key=f"conectaai_release_{payment.id}", metadata={"conectaai_proposal_id": proposal.id},
        )
    except stripe_connect.PaymentsUnavailable:
        raise PaymentRefused(503, "Os pagamentos ainda não estão disponíveis. Tente mais tarde.")
    except stripe_connect.PaymentsError as exc:
        raise PaymentRefused(502, str(exc))

    payment.status = "released"
    payment.released_at = _now()
    payment.transfer_id = transfer_id
    db.commit()
    NotificationRepository.create(
        db, user_id=creator.user_id, type_="campaignUpdate", title="Pagamento liberado",
        message=f"{company.name if company else 'A empresa'} confirmou o acordo de \"{campaign}\" e liberou {money(net)}. O valor já está na sua conta de pagamentos.",
    )
    if company is not None:
        NotificationRepository.create(
            db, user_id=company.user_id, type_="campaignUpdate", title="Pagamento liberado",
            message=f"Você confirmou o acordo de \"{campaign}\" e o pagamento foi liberado para {creator.name}.",
        )
    return payment


def set_status(db: Session, payment: PaymentDB, status: str) -> None:
    if payment.status == "pending":  # pago é final
        payment.status = status
        db.commit()


def sync(db: Session, payment: Optional[PaymentDB]) -> Optional[PaymentDB]:
    """Confere com a Stripe um pagamento ainda pendente (o app chama ao voltar da página de pagamento)."""
    if payment is None or payment.status != "pending" or not payment.checkout_session_id or not stripe_connect.is_configured():
        return payment
    try:
        status = stripe_connect.checkout_status(payment.checkout_session_id)
    except (stripe_connect.PaymentsError, stripe_connect.PaymentsUnavailable):
        return payment
    if status["paid"]:
        return mark_paid(db, payment, status["payment_intent"])
    if status["expired"]:
        set_status(db, payment, "canceled")
    return payment


def handle_event(db: Session, event: dict) -> None:
    """Avisos da Stripe sobre a página de pagamento."""
    kind = event.get("type") or ""
    if not kind.startswith("checkout.session."):
        return
    session = (event.get("data") or {}).get("object") or {}
    payment = PaymentRepository.get_by_session(db, session.get("id") or "")
    if payment is None:
        return
    status = stripe_connect.checkout_status_of(session)
    if kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded") and status["paid"]:
        mark_paid(db, payment, status["payment_intent"])
    elif kind == "checkout.session.async_payment_failed":
        set_status(db, payment, "failed")
    elif kind == "checkout.session.expired":
        set_status(db, payment, "canceled")


def to_response(db: Session, payment: PaymentDB, *, role: str) -> PaymentResponse:
    proposal = db.query(ProposalDB).filter(ProposalDB.id == payment.proposal_id).first()
    company = CompanyRepository.get_by_id(db, payment.company_id)
    creator = CreatorRepository.get_by_id(db, payment.creator_id)
    other = creator if role == "company" else company
    fee = payment.fee_cents or 0
    return PaymentResponse(
        id=payment.id,
        proposal_id=payment.proposal_id,
        campaign_name=(proposal.campaign_name if proposal else "") or "",
        counterpart_name=(other.name if other else "") or "",
        amount=payment.amount_cents / 100,
        fee=fee / 100,
        net=(payment.amount_cents - fee) / 100,
        currency=payment.currency or settings.STRIPE_CURRENCY,
        status=payment.status,
        created_at=payment.created_at,
        paid_at=payment.paid_at,
        released_at=payment.released_at,
    )


def list_for(db: Session, *, role: str, profile_id: str) -> List[PaymentResponse]:
    if not profile_id:
        return []
    return [to_response(db, p, role=role) for p in PaymentRepository.list_for(db, role=role, profile_id=profile_id)]
