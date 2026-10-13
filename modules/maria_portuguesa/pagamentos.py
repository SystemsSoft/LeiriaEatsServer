# Arquivo: modules/maria_portuguesa/pagamentos.py
#
# Pagamento para ver o contacto de uma doméstica (Stripe Checkout).
#
# Quem procura paga um valor único por contacto. A página de pagamento é a da
# própria Stripe: nenhum dado de cartão passa por este servidor. A chave é a do
# módulo e segue em cada chamada — o stripe.api_key global é do Leiria Eats.
#
# Variáveis de ambiente:
#   MP_STRIPE_SECRET_KEY         chave SECRETA da Stripe (sk_live_... ou sk_test_...)
#   MP_STRIPE_WEBHOOK_SECRET     segredo do webhook (whsec_...) de
#                                POST /maria-portuguesa/stripe/webhook (opcional)
#   MP_APP_URL                   endereço(s) do site, separados por vírgula
#                                (ex.: https://mariaportuguesa.pt). É para onde a Stripe
#                                devolve a pessoa depois de pagar
#   MP_PRECO_CONTACTO_CENTIMOS   preço de um contacto, em cêntimos (por omissão: 390)
import os
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlsplit

MP_STRIPE_SECRET_KEY = os.getenv("MP_STRIPE_SECRET_KEY", "").strip()
MP_STRIPE_WEBHOOK_SECRET = os.getenv("MP_STRIPE_WEBHOOK_SECRET", "").strip()
MP_APP_URLS = [u.strip().rstrip("/") for u in os.getenv("MP_APP_URL", "").split(",") if u.strip()]
PRECO_CONTACTO_CENTIMOS = int(os.getenv("MP_PRECO_CONTACTO_CENTIMOS") or 390)

# Marca as sessões de pagamento deste módulo: a conta Stripe pode ser partilhada.
_MODULO = "maria_portuguesa"
# Parâmetro com que a pessoa volta ao site depois de pagar.
PARAMETRO_SESSAO = "mp_sessao"


class PagamentosIndisponiveis(Exception):
    """A Stripe não está configurada neste servidor."""


class ErroPagamento(Exception):
    """A Stripe recusou a operação; a mensagem pode ser mostrada à pessoa."""


@dataclass(frozen=True)
class SessaoPagamento:
    id: str
    pago: bool
    # gid do perfil comprado; None se a sessão não for deste módulo.
    gid: Optional[str]
    valor_centimos: int
    payment_intent: Optional[str]


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


class Pagamentos:
    # Substituíveis nos testes, que não falam com a Stripe.
    _criar: Optional[Callable[..., str]] = None
    _consultar: Optional[Callable[[str], SessaoPagamento]] = None

    @classmethod
    def definir_gateway(cls, criar: Optional[Callable[..., str]], consultar: Optional[Callable[[str], SessaoPagamento]]) -> None:
        cls._criar, cls._consultar = criar, consultar

    @classmethod
    def criar_checkout(cls, *, gid: str, nome: str, retorno: str) -> str:
        """Abre a página de pagamento do contacto e devolve o endereço dela."""
        return (cls._criar or cls._criar_na_stripe)(gid=gid, nome=nome, retorno=retorno)

    @classmethod
    def consultar(cls, sessao_id: str) -> SessaoPagamento:
        return (cls._consultar or cls._consultar_na_stripe)(sessao_id)

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
    def _criar_na_stripe(cls, *, gid: str, nome: str, retorno: str) -> str:
        import stripe

        sessao = cls._chamar(
            stripe.checkout.Session.create,
            mode="payment",
            line_items=[{
                "quantity": 1,
                "price_data": {
                    "currency": "eur",
                    "unit_amount": PRECO_CONTACTO_CENTIMOS,
                    "product_data": {
                        "name": f"Contacto de {nome}",
                        "description": "Maria Portuguesa — telemóvel para falar diretamente com esta profissional.",
                    },
                },
            }],
            # A Stripe troca {CHECKOUT_SESSION_ID} pelo id verdadeiro ao devolver a pessoa.
            success_url=f"{retorno}?{PARAMETRO_SESSAO}={{CHECKOUT_SESSION_ID}}",
            cancel_url=retorno,
            locale="pt",
            custom_text={"submit": {"message": (
                "Depois de pagar, o contacto é-lhe mostrado de imediato e o serviço fica prestado nesse momento."
            )}},
            metadata={"modulo": _MODULO, "gid": gid},
        )
        return sessao.url

    @classmethod
    def _consultar_na_stripe(cls, sessao_id: str) -> SessaoPagamento:
        import stripe

        return sessao_de_objeto(cls._chamar(stripe.checkout.Session.retrieve, sessao_id))

    @staticmethod
    def evento_do_webhook(corpo: bytes, assinatura: Optional[str]) -> Optional[SessaoPagamento]:
        """Valida o aviso da Stripe e devolve a sessão paga, se for disso que se trata.
        Levanta ErroPagamento se a assinatura não for da Stripe."""
        import stripe

        if not MP_STRIPE_WEBHOOK_SECRET:
            raise PagamentosIndisponiveis("MP_STRIPE_WEBHOOK_SECRET não está definido")
        try:
            evento = stripe.Webhook.construct_event(corpo, assinatura or "", MP_STRIPE_WEBHOOK_SECRET)
        except (ValueError, stripe.SignatureVerificationError) as e:
            raise ErroPagamento(f"aviso recusado: {e}")
        if evento.type not in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            return None
        return sessao_de_objeto(evento.data.object)


def sessao_de_objeto(sessao) -> SessaoPagamento:
    """Converte a Checkout Session da Stripe no que o módulo precisa dela."""
    metadados = sessao.metadata
    metadados = metadados.to_dict() if hasattr(metadados, "to_dict") else dict(metadados or {})
    intent = getattr(sessao, "payment_intent", None)
    return SessaoPagamento(
        id=sessao.id,
        pago=getattr(sessao, "payment_status", None) == "paid",
        gid=metadados.get("gid") if metadados.get("modulo") == _MODULO else None,
        valor_centimos=int(getattr(sessao, "amount_total", None) or 0),
        payment_intent=intent if isinstance(intent, str) else getattr(intent, "id", None),
    )
