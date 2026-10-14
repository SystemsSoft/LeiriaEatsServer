# Arquivo: modules/maria_portuguesa/pagamentos.py
#
# Assinatura Premium da doméstica (Stripe Checkout + Stripe Billing).
#
# A doméstica paga uma mensalidade e o perfil dela fica em destaque nas
# pesquisas. A página de pagamento e a de gerir a assinatura (cartão, cancelar)
# são da própria Stripe: nenhum dado de cartão passa por este servidor. A chave é
# a do módulo e segue em cada chamada — o stripe.api_key global é do Leiria Eats.
#
# Variáveis de ambiente:
#   MP_STRIPE_SECRET_KEY         chave SECRETA da Stripe (sk_live_... ou sk_test_...)
#   MP_STRIPE_WEBHOOK_SECRET     segredo do webhook (whsec_...) de
#                                POST /maria-portuguesa/stripe/webhook
#   MP_APP_URL                   endereço(s) do site, separados por vírgula. É para
#                                onde a Stripe devolve a doméstica
#   MP_PRECO_PREMIUM_CENTIMOS    mensalidade, em cêntimos (por omissão: 390)
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlsplit

MP_STRIPE_SECRET_KEY = os.getenv("MP_STRIPE_SECRET_KEY", "").strip()
MP_STRIPE_WEBHOOK_SECRET = os.getenv("MP_STRIPE_WEBHOOK_SECRET", "").strip()
MP_APP_URLS = [u.strip().rstrip("/") for u in os.getenv("MP_APP_URL", "").split(",") if u.strip()]
PRECO_PREMIUM_CENTIMOS = int(os.getenv("MP_PRECO_PREMIUM_CENTIMOS") or 390)

# Marca o que é deste módulo: a conta Stripe pode ser partilhada.
_MODULO = "maria_portuguesa"
# Parâmetro com que a doméstica volta ao site depois de pagar.
PARAMETRO_SESSAO = "mp_premium"
# Página da aplicação para onde a doméstica volta (rota do editor do perfil).
_ROTA_EDITOR = "#/domestica"
# Estados da Stripe em que a assinatura ainda dá direito ao destaque. Com o
# pagamento em atraso (past_due) a Stripe ainda está a tentar cobrar: o destaque
# fica até ao fim do período já pago.
_ESTADOS_ATIVOS = {"active", "trialing", "past_due"}
# Estados de onde uma assinatura já não sai: não vale a pena voltar a perguntar por ela.
ESTADOS_TERMINADOS = {"canceled", "incomplete_expired"}


class PagamentosIndisponiveis(Exception):
    """A Stripe não está configurada neste servidor."""


class ErroPagamento(Exception):
    """A Stripe recusou a operação; a mensagem pode ser mostrada à pessoa."""


class AvisoInvalido(Exception):
    """Um pedido ao webhook que não vem da Stripe (assinatura HTTP errada)."""


@dataclass(frozen=True)
class Assinatura:
    id: str
    cliente: Optional[str]
    # Estado na Stripe: active, past_due (pagamento em atraso), canceled, ...
    estado: str
    # gid do perfil; None se a assinatura não for deste módulo.
    gid: Optional[str]
    ativa: bool
    # Fim do período pago (UTC, sem fuso, como o resto do módulo).
    valida_ate: Optional[datetime]
    # A doméstica cancelou: não renova no fim do período.
    cancela_no_fim: bool


def _origem(url: str) -> str:
    partes = urlsplit(url)
    return f"{partes.scheme}://{partes.netloc}".lower()


def url_de_retorno(pedido: Optional[str]) -> str:
    """Para onde a Stripe devolve a doméstica. O endereço pedido pelo navegador só é
    aceite se for do próprio site (MP_APP_URL) ou de desenvolvimento local — de outro
    modo a página de pagamento serviria para encaminhar pessoas para sites alheios."""
    permitidas = {_origem(u) for u in MP_APP_URLS}
    if pedido:
        partes = urlsplit(pedido.strip())
        local = partes.scheme == "http" and partes.hostname in ("localhost", "127.0.0.1")
        if partes.scheme in ("http", "https") and (local or _origem(pedido) in permitidas):
            return f"{partes.scheme}://{partes.netloc}{partes.path or '/'}"
    if MP_APP_URLS:
        return MP_APP_URLS[0] + "/"
    raise PagamentosIndisponiveis("MP_APP_URL não está definido")


def _campo(objeto, nome: str, omissao=None):
    """Lê um campo de um objeto da Stripe (StripeObject ou dict)."""
    try:
        valor = objeto[nome]
    except (KeyError, TypeError, AttributeError):
        valor = getattr(objeto, nome, omissao)
    return omissao if valor is None else valor


def assinatura_de_objeto(assinatura) -> Assinatura:
    """Converte a Subscription da Stripe no que o módulo precisa dela."""
    metadados = _campo(assinatura, "metadata", {})
    metadados = metadados.to_dict() if hasattr(metadados, "to_dict") else dict(metadados)
    fim = _campo(assinatura, "current_period_end")
    if fim is None:
        # Nas versões recentes da API o período vive em cada item da assinatura.
        itens = _campo(_campo(assinatura, "items", {}), "data", [])
        fim = _campo(itens[0], "current_period_end") if itens else None
    valida_ate = datetime.fromtimestamp(int(fim), tz=timezone.utc).replace(tzinfo=None) if fim else None
    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    cliente = _campo(assinatura, "customer")
    return Assinatura(
        id=_campo(assinatura, "id"),
        cliente=cliente if isinstance(cliente, str) else _campo(cliente, "id"),
        estado=_campo(assinatura, "status", ""),
        gid=metadados.get("gid") if metadados.get("modulo") == _MODULO else None,
        ativa=_campo(assinatura, "status") in _ESTADOS_ATIVOS and valida_ate is not None and valida_ate > agora,
        valida_ate=valida_ate,
        cancela_no_fim=bool(_campo(assinatura, "cancel_at_period_end", False)),
    )


class Pagamentos:
    # Substituíveis nos testes e no servidor de desenvolvimento, que não falam com a Stripe.
    _criar: Optional[Callable[..., str]] = None
    _consultar: Optional[Callable[[str], Optional[Assinatura]]] = None
    _portal: Optional[Callable[..., str]] = None
    _assinatura: Optional[Callable[[str], Assinatura]] = None
    _cancelar: Optional[Callable[[str], None]] = None

    @classmethod
    def definir_gateway(cls, criar=None, consultar=None, portal=None, assinatura=None, cancelar=None) -> None:
        cls._criar, cls._consultar, cls._portal = criar, consultar, portal
        cls._assinatura, cls._cancelar = assinatura, cancelar

    @staticmethod
    def configurada() -> bool:
        return bool(MP_STRIPE_SECRET_KEY) and not MP_STRIPE_SECRET_KEY.startswith("pk_")

    @classmethod
    def criar_checkout(cls, *, gid: str, email: str, cliente: Optional[str], retorno: str) -> str:
        """Abre a página de pagamento da assinatura e devolve o endereço dela."""
        return (cls._criar or cls._criar_na_stripe)(gid=gid, email=email, cliente=cliente, retorno=retorno)

    @classmethod
    def consultar_checkout(cls, sessao_id: str) -> Optional[Assinatura]:
        """A assinatura criada por esta sessão de pagamento; None se ainda não há."""
        return (cls._consultar or cls._consultar_na_stripe)(sessao_id)

    @classmethod
    def portal(cls, *, cliente: str, retorno: str) -> str:
        """Página da Stripe onde a doméstica muda o cartão, vê faturas ou cancela."""
        return (cls._portal or cls._portal_na_stripe)(cliente=cliente, retorno=retorno)

    @classmethod
    def consultar_assinatura(cls, assinatura_id: str) -> Assinatura:
        """Estado atual da assinatura, perguntado à Stripe (não ao que veio num aviso)."""
        return (cls._assinatura or cls._assinatura_na_stripe)(assinatura_id)

    @classmethod
    def cancelar(cls, assinatura_id: str) -> None:
        """Cancela já a assinatura, sem mais cobranças. Não faz nada se já tiver terminado."""
        (cls._cancelar or cls._cancelar_na_stripe)(assinatura_id)

    # ── Stripe ───────────────────────────────────────────────────────────────

    @staticmethod
    def _chave() -> str:
        # pk_ é a chave publicável: não serve no servidor.
        if not MP_STRIPE_SECRET_KEY or MP_STRIPE_SECRET_KEY.startswith("pk_"):
            raise PagamentosIndisponiveis("MP_STRIPE_SECRET_KEY não está definida (tem de ser a chave secreta, sk_...)")
        return MP_STRIPE_SECRET_KEY

    @classmethod
    def _chamar(cls, funcao, *args, **kwargs):
        import stripe  # import tardio, como no resto do módulo

        chave = cls._chave()
        try:
            return funcao(*args, api_key=chave, **kwargs)
        except stripe.StripeError as e:
            print(f"⚠️ [MariaPortuguesa] Erro da Stripe: {e}")
            raise ErroPagamento("Não foi possível falar com o serviço de pagamentos. Tente de novo dentro de instantes.")

    @classmethod
    def _criar_na_stripe(cls, *, gid: str, email: str, cliente: Optional[str], retorno: str) -> str:
        import stripe

        metadados = {"modulo": _MODULO, "gid": gid}
        # Quem já foi assinante reutiliza o cliente da Stripe (cartão e faturas no mesmo sítio).
        quem = {"customer": cliente} if cliente else {"customer_email": email}
        sessao = cls._chamar(
            stripe.checkout.Session.create,
            mode="subscription",
            line_items=[{
                "quantity": 1,
                "price_data": {
                    "currency": "eur",
                    "unit_amount": PRECO_PREMIUM_CENTIMOS,
                    "recurring": {"interval": "month"},
                    "product_data": {
                        "name": "Maria Portuguesa Premium",
                        "description": "Perfil em destaque nas pesquisas das famílias.",
                    },
                },
            }],
            client_reference_id=gid,
            metadata=metadados,
            subscription_data={"metadata": metadados},
            # A Stripe troca {CHECKOUT_SESSION_ID} pelo id verdadeiro ao devolver a pessoa.
            success_url=f"{retorno}?{PARAMETRO_SESSAO}={{CHECKOUT_SESSION_ID}}{_ROTA_EDITOR}",
            cancel_url=f"{retorno}{_ROTA_EDITOR}",
            locale="pt",
            **quem,
        )
        return _campo(sessao, "url")

    @classmethod
    def _consultar_na_stripe(cls, sessao_id: str) -> Optional[Assinatura]:
        import stripe

        sessao = cls._chamar(stripe.checkout.Session.retrieve, sessao_id, expand=["subscription"])
        assinatura = _campo(sessao, "subscription")
        if not assinatura:
            return None
        if isinstance(assinatura, str):
            assinatura = cls._chamar(stripe.Subscription.retrieve, assinatura)
        return assinatura_de_objeto(assinatura)

    @classmethod
    def _assinatura_na_stripe(cls, assinatura_id: str) -> Assinatura:
        import stripe

        return assinatura_de_objeto(cls._chamar(stripe.Subscription.retrieve, assinatura_id))

    @classmethod
    def _cancelar_na_stripe(cls, assinatura_id: str) -> None:
        import stripe

        if cls._assinatura_na_stripe(assinatura_id).estado in ESTADOS_TERMINADOS:
            return
        cls._chamar(stripe.Subscription.cancel, assinatura_id)

    @classmethod
    def _portal_na_stripe(cls, *, cliente: str, retorno: str) -> str:
        import stripe

        sessao = cls._chamar(
            stripe.billing_portal.Session.create, customer=cliente, return_url=f"{retorno}{_ROTA_EDITOR}",
            locale="pt",
        )
        return _campo(sessao, "url")

    @classmethod
    def evento_do_webhook(cls, corpo: bytes, assinatura_http: Optional[str]) -> Optional[Assinatura]:
        """Valida o aviso da Stripe e devolve o estado ATUAL da assinatura a que ele se
        refere, se for disso que se trata. Levanta AvisoInvalido se a assinatura HTTP não
        for da Stripe, e ErroPagamento se não conseguir perguntar à Stripe (o aviso volta
        a ser entregue mais tarde)."""
        import stripe

        if not MP_STRIPE_WEBHOOK_SECRET:
            raise PagamentosIndisponiveis("MP_STRIPE_WEBHOOK_SECRET não está definido")
        try:
            evento = stripe.Webhook.construct_event(corpo, assinatura_http or "", MP_STRIPE_WEBHOOK_SECRET)
        except (ValueError, stripe.SignatureVerificationError) as e:
            raise AvisoInvalido(str(e))
        objeto = evento.data.object
        if evento.type in ("customer.subscription.created", "customer.subscription.updated",
                           "customer.subscription.deleted"):
            # A Stripe pode entregar os avisos fora de ordem: um "updated" antigo a chegar
            # depois do "deleted" reativaria o destaque. Por isso não se confia no que vem
            # no aviso e pergunta-se o estado atual. Os de outros produtos ficam de fora.
            if assinatura_de_objeto(objeto).gid is None:
                return None
            return cls.consultar_assinatura(_campo(objeto, "id"))
        if evento.type == "checkout.session.completed" and _campo(objeto, "mode") == "subscription":
            return cls.consultar_checkout(_campo(objeto, "id"))
        return None
