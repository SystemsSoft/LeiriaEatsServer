# Arquivo: modules/maria_portuguesa/pagamentos.py
#
# Pagamentos do marketplace (Stripe Connect, «cobranças e transferências separadas»).
#
# A família paga um acordo pela Stripe Checkout; o dinheiro fica no saldo da
# plataforma. Quando a família confirma que o serviço foi feito (ou passado o prazo
# de libertação automática), a plataforma transfere para a conta Connect da
# doméstica o valor menos a comissão. A doméstica cria essa conta (Express) nas
# páginas da própria Stripe: nenhum dado de cartão ou bancário passa por este
# servidor. A chave é a do módulo e segue em cada chamada — o stripe.api_key global
# é do Leiria Eats.
#
# Variáveis de ambiente:
#   MP_STRIPE_SECRET_KEY         chave SECRETA da Stripe (sk_live_... ou sk_test_...)
#   MP_STRIPE_WEBHOOK_SECRET     segredo(s) do webhook (whsec_...) de
#                                POST /maria-portuguesa/stripe/webhook, separados por
#                                vírgula: o endpoint da conta e o das contas ligadas
#                                (Connect) têm segredos diferentes
#   MP_APP_URL                   endereço(s) do site, separados por vírgula. É para
#                                onde a Stripe devolve quem paga ou ativa a conta
#   MP_COMISSAO_PERCENT          comissão da plataforma sobre cada acordo, em % (10)
#   MP_DIAS_LIBERTACAO           dias depois do serviço até o pagamento seguir sozinho
#                                para a doméstica, se a família não o fizer (7)
import os
from dataclasses import dataclass
from typing import Optional, Protocol
from urllib.parse import urlsplit

MP_STRIPE_SECRET_KEY = os.getenv("MP_STRIPE_SECRET_KEY", "").strip()
MP_STRIPE_WEBHOOK_SECRETS = [s.strip() for s in os.getenv("MP_STRIPE_WEBHOOK_SECRET", "").split(",") if s.strip()]
MP_APP_URLS = [u.strip().rstrip("/") for u in os.getenv("MP_APP_URL", "").split(",") if u.strip()]
COMISSAO_PERCENT = float(os.getenv("MP_COMISSAO_PERCENT") or 10)
DIAS_LIBERTACAO = int(os.getenv("MP_DIAS_LIBERTACAO") or 7)

# Marca o que é deste módulo: a conta Stripe pode ser partilhada.
_MODULO = "maria_portuguesa"
# Parâmetro com que a família volta ao site depois de pagar.
PARAMETRO_SESSAO = "mp_acordo"
# Páginas da aplicação para onde a Stripe devolve cada um.
ROTA_FAMILIA = "#/familia"
ROTA_DOMESTICA = "#/domestica"
# Atividade da doméstica para a Stripe: limpeza e manutenção (MCC 7349).
_MCC_LIMPEZA = "7349"


class PagamentosIndisponiveis(Exception):
    """A Stripe não está configurada neste servidor."""


class ErroPagamento(Exception):
    """A Stripe recusou a operação; a mensagem pode ser mostrada à pessoa."""


class AvisoInvalido(Exception):
    """Um pedido ao webhook que não vem da Stripe (assinatura HTTP errada)."""


@dataclass(frozen=True)
class PagamentoCheckout:
    """Uma sessão da Stripe Checkout de um acordo."""
    sessao: str
    # gid do acordo; None se a sessão não for deste módulo.
    acordo: Optional[str]
    pago: bool
    pagamento_id: Optional[str] = None   # PaymentIntent (pi_...)
    cobranca_id: Optional[str] = None    # Charge (ch_...), origem da transferência


@dataclass(frozen=True)
class Aviso:
    """O que um aviso da Stripe muda neste módulo."""
    # "pago" (checkout), "reembolsado" (pagamento devolvido) ou "conta" (conta Connect mudou)
    tipo: str
    checkout: Optional[PagamentoCheckout] = None
    pagamento_id: Optional[str] = None
    conta_id: Optional[str] = None


def comissao(valor_centimos: int) -> int:
    """A parte da plataforma num acordo deste valor, em cêntimos."""
    return round(valor_centimos * COMISSAO_PERCENT / 100)


def _origem(url: str) -> str:
    partes = urlsplit(url)
    return f"{partes.scheme}://{partes.netloc}".lower()


def url_de_retorno(pedido: Optional[str]) -> str:
    """Para onde a Stripe devolve a pessoa. O endereço pedido pelo navegador só é
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


def _id(objeto) -> Optional[str]:
    """Id de um campo da Stripe que tanto pode vir como texto como expandido."""
    if objeto is None or isinstance(objeto, str):
        return objeto
    return _campo(objeto, "id")


def _metadados(objeto) -> dict:
    metadados = _campo(objeto, "metadata", {})
    return metadados.to_dict() if hasattr(metadados, "to_dict") else dict(metadados)


class Gateway(Protocol):
    """O que o módulo precisa da Stripe. Os testes e o servidor de desenvolvimento
    passam uma implementação falsa (Pagamentos.definir_gateway)."""

    def criar_conta(self, *, gid: str, email: str) -> str: ...
    def link_de_ativacao(self, *, conta: str, retorno: str) -> str: ...
    def conta_pronta(self, conta: str) -> bool: ...
    def link_do_painel(self, conta: str) -> str: ...
    def criar_checkout(self, *, acordo: str, valor_centimos: int, nome: str, descricao: str,
                       email: str, retorno: str) -> tuple[str, str]: ...
    def consultar_checkout(self, sessao: str) -> PagamentoCheckout: ...
    def transferir(self, *, conta: str, valor_centimos: int, acordo: str, cobranca: str) -> str: ...


class Pagamentos:
    _gateway: Optional[Gateway] = None

    @classmethod
    def definir_gateway(cls, gateway: Optional[Gateway] = None) -> None:
        cls._gateway = gateway

    @classmethod
    def _g(cls) -> Gateway:
        return cls._gateway or _GatewayStripe

    @staticmethod
    def configurada() -> bool:
        return bool(MP_STRIPE_SECRET_KEY) and not MP_STRIPE_SECRET_KEY.startswith("pk_")

    # ── Conta da doméstica (Connect) ────────────────────────────────────────

    @classmethod
    def criar_conta(cls, *, gid: str, email: str) -> str:
        """Cria a conta Connect (Express) onde a doméstica vai receber. Devolve o id."""
        return cls._g().criar_conta(gid=gid, email=email)

    @classmethod
    def link_de_ativacao(cls, *, conta: str, retorno: str) -> str:
        """Páginas da Stripe onde a doméstica indica a identidade e o IBAN."""
        return cls._g().link_de_ativacao(conta=conta, retorno=retorno)

    @classmethod
    def conta_pronta(cls, conta: str) -> bool:
        """A conta já pode receber transferências."""
        return cls._g().conta_pronta(conta)

    @classmethod
    def link_do_painel(cls, conta: str) -> str:
        """Painel Express da doméstica: transferências recebidas e dados bancários."""
        return cls._g().link_do_painel(conta)

    # ── Pagamento da família ────────────────────────────────────────────────

    @classmethod
    def criar_checkout(cls, *, acordo: str, valor_centimos: int, nome: str, descricao: str,
                       email: str, retorno: str) -> tuple[str, str]:
        """Abre a página de pagamento de um acordo. Devolve (id da sessão, endereço)."""
        return cls._g().criar_checkout(acordo=acordo, valor_centimos=valor_centimos, nome=nome,
                                       descricao=descricao, email=email, retorno=retorno)

    @classmethod
    def consultar_checkout(cls, sessao: str) -> PagamentoCheckout:
        return cls._g().consultar_checkout(sessao)

    @classmethod
    def transferir(cls, *, conta: str, valor_centimos: int, acordo: str, cobranca: str) -> str:
        """Liberta o pagamento retido: transfere para a conta da doméstica. Repetir a
        chamada para o mesmo acordo não transfere duas vezes."""
        return cls._g().transferir(conta=conta, valor_centimos=valor_centimos, acordo=acordo, cobranca=cobranca)

    # ── Avisos (webhook) ────────────────────────────────────────────────────

    @classmethod
    def evento_do_webhook(cls, corpo: bytes, assinatura_http: Optional[str]) -> Optional[Aviso]:
        """Valida o aviso da Stripe e diz o que ele muda. Levanta AvisoInvalido se a
        assinatura HTTP não for da Stripe, e ErroPagamento se não conseguir confirmar
        o estado com a Stripe (o aviso volta a ser entregue mais tarde)."""
        import stripe

        if not MP_STRIPE_WEBHOOK_SECRETS:
            raise PagamentosIndisponiveis("MP_STRIPE_WEBHOOK_SECRET não está definido")
        evento, recusa = None, None
        for segredo in MP_STRIPE_WEBHOOK_SECRETS:
            try:
                evento = stripe.Webhook.construct_event(corpo, assinatura_http or "", segredo)
                break
            except stripe.SignatureVerificationError as e:
                recusa = e
            except ValueError as e:
                raise AvisoInvalido(str(e))
        if evento is None:
            raise AvisoInvalido(str(recusa))

        objeto = evento.data.object
        if evento.type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            if _campo(objeto, "mode") != "payment" or _metadados(objeto).get("modulo") != _MODULO:
                return None
            # Não se confia no que vem no aviso: pergunta-se à Stripe o estado atual.
            return Aviso("pago", checkout=cls.consultar_checkout(_campo(objeto, "id")))
        if evento.type == "charge.refunded":
            # Só o reembolso total desfaz o acordo; um parcial fica para tratar à mão.
            if _campo(objeto, "amount_refunded", 0) < _campo(objeto, "amount", 0):
                return None
            return Aviso("reembolsado", pagamento_id=_id(_campo(objeto, "payment_intent")))
        if evento.type == "account.updated":
            return Aviso("conta", conta_id=_campo(objeto, "id"))
        return None


class _GatewayStripe:
    """A Stripe verdadeira."""

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
    def criar_conta(cls, *, gid: str, email: str) -> str:
        import stripe

        conta = cls._chamar(
            stripe.Account.create,
            type="express",
            country="PT",
            email=email,
            business_type="individual",
            capabilities={"card_payments": {"requested": True}, "transfers": {"requested": True}},
            business_profile={
                "mcc": _MCC_LIMPEZA,
                "product_description": "Serviços domésticos contratados através da Maria Portuguesa.",
            },
            metadata={"modulo": _MODULO, "gid": gid},
            # Uma conta por doméstica, mesmo que o pedido chegue duas vezes.
            idempotency_key=f"mp-conta-{gid}",
        )
        return _campo(conta, "id")

    @classmethod
    def link_de_ativacao(cls, *, conta: str, retorno: str) -> str:
        import stripe

        link = cls._chamar(
            stripe.AccountLink.create,
            account=conta,
            type="account_onboarding",
            # O link é de uso único e expira: a Stripe manda para refresh_url, que volta
            # ao editor, onde «Continuar a ativação» pede um link novo.
            refresh_url=f"{retorno}{ROTA_DOMESTICA}",
            return_url=f"{retorno}{ROTA_DOMESTICA}",
        )
        return _campo(link, "url")

    @classmethod
    def conta_pronta(cls, conta: str) -> bool:
        import stripe

        dados = cls._chamar(stripe.Account.retrieve, conta)
        capacidades = _campo(dados, "capabilities", {})
        return bool(_campo(dados, "details_submitted", False)) and _campo(capacidades, "transfers") == "active"

    @classmethod
    def link_do_painel(cls, conta: str) -> str:
        import stripe

        return _campo(cls._chamar(stripe.Account.create_login_link, conta), "url")

    @classmethod
    def criar_checkout(cls, *, acordo: str, valor_centimos: int, nome: str, descricao: str,
                       email: str, retorno: str) -> tuple[str, str]:
        import stripe

        metadados = {"modulo": _MODULO, "acordo": acordo}
        sessao = cls._chamar(
            stripe.checkout.Session.create,
            mode="payment",
            line_items=[{
                "quantity": 1,
                "price_data": {
                    "currency": "eur",
                    "unit_amount": valor_centimos,
                    "product_data": {"name": f"Serviço doméstico com {nome}", "description": descricao[:300]},
                },
            }],
            client_reference_id=acordo,
            metadata=metadados,
            payment_intent_data={"metadata": metadados, "transfer_group": acordo,
                                 "description": f"Maria Portuguesa — acordo {acordo}"},
            customer_email=email,
            # A Stripe troca {CHECKOUT_SESSION_ID} pelo id verdadeiro ao devolver a pessoa.
            success_url=f"{retorno}?{PARAMETRO_SESSAO}={{CHECKOUT_SESSION_ID}}{ROTA_FAMILIA}",
            cancel_url=f"{retorno}{ROTA_FAMILIA}",
            locale="pt",
        )
        return _campo(sessao, "id"), _campo(sessao, "url")

    @classmethod
    def consultar_checkout(cls, sessao: str) -> PagamentoCheckout:
        import stripe

        dados = cls._chamar(stripe.checkout.Session.retrieve, sessao, expand=["payment_intent"])
        metadados = _metadados(dados)
        intencao = _campo(dados, "payment_intent")
        return PagamentoCheckout(
            sessao=_campo(dados, "id"),
            acordo=metadados.get("acordo") if metadados.get("modulo") == _MODULO else None,
            pago=_campo(dados, "payment_status") == "paid",
            pagamento_id=_id(intencao),
            cobranca_id=_id(_campo(intencao, "latest_charge")) if intencao and not isinstance(intencao, str) else None,
        )

    @classmethod
    def transferir(cls, *, conta: str, valor_centimos: int, acordo: str, cobranca: str) -> str:
        import stripe

        transferencia = cls._chamar(
            stripe.Transfer.create,
            amount=valor_centimos,
            currency="eur",
            destination=conta,
            transfer_group=acordo,
            # Liga a transferência à cobrança: pode seguir antes de o dinheiro estar
            # disponível no saldo da plataforma.
            source_transaction=cobranca,
            metadata={"modulo": _MODULO, "acordo": acordo},
            idempotency_key=f"mp-libertar-{acordo}",
        )
        return _campo(transferencia, "id")
