# Arquivo: conectaai/services/payments/stripe_connect.py
#
# Tudo o que o ConectaAI fala com a Stripe Connect passa por aqui (os testes trocam estas funções por falsas).
#
# Modelo: contas conectadas do tipo Express — o cadastro (dados da empresa, sócios, conta bancária, documentos) e
# o painel de saques são páginas da própria Stripe; o ConectaAI só cria a conta, manda o usuário para o link e
# guarda a situação. Nenhum dado bancário passa pelo nosso servidor.
import json
import logging
import re
from typing import Optional

import stripe

from conectaai.core.config import settings

logger = logging.getLogger("conectaai.payments")


class PaymentsUnavailable(Exception):
    """A Stripe não está configurada neste servidor (ou a chave não é a secreta)."""


class PaymentsError(Exception):
    """A Stripe recusou a operação; a mensagem pode ser mostrada ao usuário."""


def is_configured() -> bool:
    key = settings.STRIPE_API_KEY
    return bool(key) and not key.startswith("pk_")  # pk_ = chave publicável, não serve no servidor


def _key() -> str:
    if not is_configured():
        if settings.STRIPE_API_KEY.startswith("pk_"):
            logger.error("CONECTAAI_STRIPE_SECRET_KEY precisa ser a chave SECRETA (sk_...), não a publicável (pk_...)")
        raise PaymentsUnavailable()
    return settings.STRIPE_API_KEY


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, api_key=_key(), **kwargs)
    except stripe.error.StripeError as exc:  # type: ignore[attr-defined]
        logger.warning("Erro da Stripe: %s", exc)
        raise PaymentsError(getattr(exc, "user_message", None) or "A Stripe recusou a operação. Tente de novo em instantes.")


def create_account(*, email: str, role: str, user_id: str, country: str) -> str:
    """Cria a conta conectada (Express) e devolve o id (acct_...)."""
    account = _call(
        stripe.Account.create,
        type="express",
        country=country,
        email=email or None,
        business_type="company" if role == "company" else "individual",
        capabilities={"card_payments": {"requested": True}, "transfers": {"requested": True}},
        metadata={"conectaai_user_id": user_id, "conectaai_role": role},
    )
    return account["id"]


def create_onboarding_link(account_id: str, *, return_url: str, refresh_url: str) -> str:
    """Link (de uso único, expira em minutos) para o cadastro na Stripe."""
    link = _call(
        stripe.AccountLink.create,
        account=account_id,
        return_url=return_url,
        refresh_url=refresh_url,
        type="account_onboarding",
    )
    return link["url"]


def create_dashboard_link(account_id: str) -> str:
    """Link para o painel Express da Stripe (saldo, saques, dados bancários)."""
    return _call(stripe.Account.create_login_link, account_id)["url"]


def create_payment_checkout(
    *, amount_cents: int, currency: str, description: str, transfer_group: str,
    success_url: str, cancel_url: str, customer_email: str, metadata: dict,
) -> dict:
    """Página de pagamento da Stripe para a empresa pagar um acordo. A cobrança entra na conta da PLATAFORMA e o
    valor fica reservado: o repasse ao creator é um passo separado ([release_to_creator]), feito quando a empresa
    confirma que o acordo foi cumprido. [transfer_group] liga a cobrança ao repasse. Devolve {"id", "url"}."""
    intent = {"transfer_group": transfer_group, "metadata": metadata}
    session = _call(
        stripe.checkout.Session.create,
        mode="payment",
        line_items=[{"quantity": 1, "price_data": {"currency": currency, "unit_amount": amount_cents, "product_data": {"name": description}}}],
        payment_intent_data=intent,
        customer_email=customer_email or None,
        success_url=success_url,
        cancel_url=cancel_url,
        locale="pt",  # português de Portugal na página de pagamento
        metadata=metadata,
    )
    return {"id": session["id"], "url": session["url"]}


def release_to_creator(
    *, amount_cents: int, currency: str, destination: str, payment_intent_id: str, transfer_group: str,
    idempotency_key: str, metadata: dict,
) -> str:
    """Repassa [amount_cents] (o valor do acordo menos a taxa da plataforma) para a conta conectada do creator.
    Sai da cobrança original (`source_transaction`), então funciona mesmo antes de o dinheiro estar disponível no
    saldo da plataforma. [idempotency_key]: a mesma liberação pedida duas vezes gera um repasse só. Devolve tr_..."""
    intent = _plain(_call(stripe.PaymentIntent.retrieve, payment_intent_id))
    charge = intent.get("latest_charge")
    charge_id = charge if isinstance(charge, str) else ((charge or {}).get("id") or "")
    if not charge_id:
        raise PaymentsError("A Stripe ainda não registrou a cobrança deste pagamento. Tente de novo em instantes.")
    transfer = _call(
        stripe.Transfer.create,
        amount=amount_cents,
        currency=currency,
        destination=destination,
        source_transaction=charge_id,
        transfer_group=transfer_group,
        metadata=metadata,
        idempotency_key=idempotency_key,
    )
    return transfer["id"]


def checkout_status(session_id: str) -> dict:
    """{"paid": bool, "expired": bool, "payment_intent": str} da sessão de pagamento."""
    return checkout_status_of(_call(stripe.checkout.Session.retrieve, session_id))


def checkout_status_of(session) -> dict:
    session = _plain(session)
    intent = session.get("payment_intent")
    return {
        "paid": session.get("payment_status") == "paid",
        "expired": session.get("status") == "expired",
        "payment_intent": intent if isinstance(intent, str) else ((intent or {}).get("id") or ""),
    }


def account_status(account_id: str) -> dict:
    return status_of(_call(stripe.Account.retrieve, account_id))


def _plain(obj) -> dict:
    """Objeto da biblioteca da Stripe → dict simples (nas versões novas ele não é dict nem tem .get)."""
    return obj if isinstance(obj, dict) else obj.to_dict()


def status_of(account) -> dict:
    """Do objeto `account` da Stripe (resposta da API ou corpo do webhook) para os campos que guardamos."""
    account = _plain(account)
    requirements = account.get("requirements") or {}
    due = list(requirements.get("currently_due") or []) + list(requirements.get("past_due") or [])
    return {
        "details_submitted": bool(account.get("details_submitted")),
        "charges_enabled": bool(account.get("charges_enabled")),
        "payouts_enabled": bool(account.get("payouts_enabled")),
        "requirements_due": sorted(set(due)),
    }


def parse_webhook(payload: bytes, signature: Optional[str]) -> dict:
    """Confere a assinatura do aviso (Stripe-Signature) e devolve o evento. ValueError se não conferir."""
    # dois endpoints na Stripe (contas conectadas e a própria conta) = dois segredos; o aviso vale se conferir com um
    secrets = [s.strip() for s in (settings.STRIPE_WEBHOOK_SECRET or "").split(",") if s.strip()]
    if not secrets:
        raise PaymentsUnavailable()
    try:
        text = payload.decode("utf-8")
        # a biblioteca confere só a assinatura (HMAC e validade de 5 min); o JSON é lido aqui, como dict simples —
        # os objetos da biblioteca mudam de formato entre versões
        last_error = None
        for secret in secrets:
            try:
                stripe.WebhookSignature.verify_header(text, signature or "", secret, 300)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        event = json.loads(text)
        if not isinstance(event, dict) or "type" not in event:
            raise ValueError("evento sem tipo")
        return event
    except Exception as exc:  # assinatura inválida, corpo que não é JSON, aviso antigo
        raise ValueError(str(exc))


_SAFE_URL = re.compile(r"^(https://[^\s/?#]+|http://(localhost|127\.0\.0\.1)(:\d+)?)(/[^\s]*)?$")


def safe_app_url(url: Optional[str], fallback_path: str) -> Optional[str]:
    """Só https (ou localhost, para desenvolver). Sem endereço do app, usa CONECTAAI_APP_URL."""
    candidate = (url or "").strip() or (f"{settings.APP_URL}{fallback_path}" if settings.APP_URL else "")
    return candidate if candidate and _SAFE_URL.match(candidate) else None
