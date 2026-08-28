"""
Testes do pagamento em 2 etapas e repasse multi-restaurante (PLANO_PAGAMENTO_2_ETAPAS.md).

Usa SQLite em memória + dublês do módulo `stripe` (substituindo PaymentIntent.capture/
cancel, Transfer.create/create_reversal e Refund.create diretamente na classe) — não
chama a API real, para a suíte ficar determinística e sem rede. A mecânica do Stripe em
si (capture_method="manual", eventos de webhook, Transfer com source_transaction,
Transfer.create_reversal) foi validada ao vivo em modo de teste durante o
desenvolvimento; estes testes cobrem a LÓGICA DE NEGÓCIO em cima dela.

Execução:
    python3 tests/test_pagamento_2_etapas.py
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

import stripe
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base
from core.sql_models import OrderDB, SubOrderDB, RestaurantDB
from schemas.models import OrderStatusUpdate
from api.routes.order_routes import (
    _liquidar_pedido_se_todos_responderam,
    _repassar_para_restaurantes,
    _reverter_repasses_do_pedido,
    accept_sub_order,
    decline_sub_order,
    update_sub_order_status,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _montar_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _restaurante(db, gid, name, plan="ESSENCE", use_own_delivery=False,
                  stripe_account_id="acct_teste", onboarding=True):
    r = RestaurantDB(
        name=name, category="X", login=f"login_{gid}", password="x", gid=gid,
        plan=plan, use_own_delivery=use_own_delivery,
        stripe_account_id=stripe_account_id, stripe_onboarding_completed=onboarding,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _pedido_manual_capture(db, sub_orders_spec):
    """sub_orders_spec: lista de dicts com restaurant_gid, restaurant_name, total,
    delivery_fee, estado ("pendente" default / "aceito" / "recusado").

    IMPORTANTE (2026-08-28): `estado` é só um atalho de fixture — o estado real do
    fluxo de aceite não é mais um valor de `sub.status` (esse campo só usa o
    vocabulário que os apps já conhecem: "Pendente"/"Cancelado"). "aceito" grava
    `accepted_at`; "recusado" grava `declined_at`/`status="Cancelado"`.
    """
    master = OrderDB(
        gid="01ORDER_TESTE", customer_name="Cliente Teste", delivery_address="Rua X",
        status="Pendente", total=0.0, user_id="U-1",
        payment_intent_id="pi_teste_123", payment_flow="MANUAL_CAPTURE",
        payment_status="AUTHORIZED", total_service_fee=2.0, total_delivery_fee=0.0,
    )
    db.add(master)
    db.commit()
    db.refresh(master)

    for i, spec in enumerate(sub_orders_spec):
        estado = spec.get("estado", "pendente")
        sub = SubOrderDB(
            gid=f"01SUB_{i}", master_order_gid=master.gid,
            restaurant_gid=spec["restaurant_gid"], restaurant_name=spec.get("restaurant_name", ""),
            status="Cancelado" if estado == "recusado" else "Pendente",
            accepted_at=datetime.now(timezone.utc) if estado == "aceito" else None,
            declined_at=datetime.now(timezone.utc) if estado == "recusado" else None,
            decline_reason="motivo de teste" if estado == "recusado" else None,
            total=spec["total"], delivery_fee=spec.get("delivery_fee", 0.0),
        )
        db.add(sub)
    master.total = sum(s["total"] for s in sub_orders_spec) + master.total_service_fee
    db.commit()
    db.refresh(master)
    return master


class _StripeMocks:
    """Substitui os métodos do módulo stripe usados pelo fluxo de pagamento, e
    restaura os originais ao sair (context manager)."""

    def __init__(self):
        self._originais = {}
        self.capturas = []
        self.cancelamentos = []
        self.transfers = []
        self.reversoes = []
        self.refunds = []

    def __enter__(self):
        self._originais["capture"] = stripe.PaymentIntent.capture
        self._originais["cancel"] = stripe.PaymentIntent.cancel
        self._originais["transfer_create"] = stripe.Transfer.create
        self._originais["transfer_reversal"] = stripe.Transfer.create_reversal
        self._originais["refund_create"] = stripe.Refund.create
        self._originais["pi_retrieve"] = stripe.PaymentIntent.retrieve

        def fake_capture(pi_id, **kwargs):
            self.capturas.append({"pi_id": pi_id, **kwargs})
            class _Fake:
                id = pi_id
                status = "succeeded"
            return _Fake()

        def fake_cancel(pi_id, **kwargs):
            self.cancelamentos.append({"pi_id": pi_id, **kwargs})
            class _Fake:
                id = pi_id
                status = "canceled"
            return _Fake()

        def fake_transfer_create(**kwargs):
            self.transfers.append(kwargs)
            class _Fake:
                id = f"tr_fake_{len(self.transfers)}"
                amount = kwargs.get("amount")
                destination = kwargs.get("destination")
            return _Fake()

        def fake_transfer_reversal(transfer_id, **kwargs):
            self.reversoes.append({"transfer_id": transfer_id, **kwargs})
            class _Fake:
                id = f"trr_fake_{len(self.reversoes)}"
                amount = kwargs.get("amount")
            return _Fake()

        def fake_refund_create(**kwargs):
            self.refunds.append(kwargs)
            class _Fake:
                id = f"re_fake_{len(self.refunds)}"
                status = "succeeded"
            return _Fake()

        def fake_pi_retrieve(pi_id, **kwargs):
            class _Fake:
                id = pi_id
                status = "requires_capture"
            return _Fake()

        stripe.PaymentIntent.capture = fake_capture
        stripe.PaymentIntent.cancel = fake_cancel
        stripe.Transfer.create = fake_transfer_create
        stripe.Transfer.create_reversal = fake_transfer_reversal
        stripe.Refund.create = fake_refund_create
        stripe.PaymentIntent.retrieve = fake_pi_retrieve
        return self

    def __exit__(self, *exc):
        stripe.PaymentIntent.capture = self._originais["capture"]
        stripe.PaymentIntent.cancel = self._originais["cancel"]
        stripe.Transfer.create = self._originais["transfer_create"]
        stripe.Transfer.create_reversal = self._originais["transfer_reversal"]
        stripe.Refund.create = self._originais["refund_create"]
        stripe.PaymentIntent.retrieve = self._originais["pi_retrieve"]


# ── 1. _liquidar_pedido_se_todos_responderam ────────────────────────────────

def teste_liquidacao_nao_age_com_sub_pedido_pendente():
    db = _montar_db()
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "aceito"},
        {"restaurant_gid": "01R_B", "total": 15.0, "estado": "pendente"},
    ])
    with _StripeMocks() as m:
        resultado = _liquidar_pedido_se_todos_responderam(master, db)
        assert resultado["acao"] == "aguardando"
        assert len(m.capturas) == 0 and len(m.cancelamentos) == 0
    print("OK  - liquidação não age enquanto houver sub-pedido sem resposta (accepted_at/declined_at nulos)")


def teste_liquidacao_cancela_quando_nenhum_aceita():
    db = _montar_db()
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "recusado"},
        {"restaurant_gid": "01R_B", "total": 15.0, "estado": "recusado"},
    ])
    with _StripeMocks() as m:
        resultado = _liquidar_pedido_se_todos_responderam(master, db)
        assert resultado["acao"] == "cancelado"
        assert len(m.cancelamentos) == 1
        assert len(m.capturas) == 0
    assert master.status == "Cancelado"
    assert master.payment_status == "CANCELED"
    print("OK  - nenhum restaurante aceita -> PaymentIntent cancelado, sem captura, sem reembolso")


def teste_liquidacao_captura_produtos_aceitos_mais_taxa_de_servico_integral():
    """A decisão financeira fechada: taxa de serviço INTEGRAL mesmo com recusa parcial."""
    db = _montar_db()
    # A (20) aceita, B (15) aceita, C (25 + entrega 4) recusa. Taxa de serviço: 2.0 (integral).
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "aceito"},
        {"restaurant_gid": "01R_B", "total": 15.0, "estado": "aceito"},
        {"restaurant_gid": "01R_C", "total": 29.0, "estado": "recusado"},
    ])
    with _StripeMocks() as m:
        resultado = _liquidar_pedido_se_todos_responderam(master, db)
        assert resultado["acao"] == "captura_solicitada"
        assert len(m.capturas) == 1
        # 20 + 15 + 2.0 (serviço integral) = 37.0 -> 3700 cêntimos
        assert m.capturas[0]["amount_to_capture"] == 3700, m.capturas[0]
    print("OK  - captura = produtos dos aceitos + taxa de serviço INTEGRAL (decisão financeira fechada)")


def teste_liquidacao_e_idempotente():
    db = _montar_db()
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "aceito"},
    ])
    with _StripeMocks() as m:
        _liquidar_pedido_se_todos_responderam(master, db)
        assert len(m.capturas) == 1
        resultado2 = _liquidar_pedido_se_todos_responderam(master, db)
        assert resultado2["acao"] == "ja_liquidado"
        assert len(m.capturas) == 1, "não deveria capturar de novo"
    print("OK  - chamar a liquidação 2x não captura em dobro (payment_status já não é AUTHORIZED)")


# ── 2. accept_sub_order / decline_sub_order ─────────────────────────────────

def teste_accept_sub_order_marca_aceito_e_liquida_se_for_o_ultimo():
    db = _montar_db()
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "pendente"},
    ])
    sub = master.sub_orders[0]
    with _StripeMocks() as m:
        resultado = accept_sub_order(sub.gid, db)
        # sub.status permanece "Pendente" — o app não sabe que existe um conceito de
        # aceite; o accepted_at (checado abaixo) é quem carrega o estado real.
        assert resultado["status"] == "Pendente"
        assert sub.accepted_at is not None
        assert resultado["liquidacao"]["acao"] == "captura_solicitada"
        assert len(m.capturas) == 1
    print("OK  - accept_sub_order grava accepted_at (sem mudar status visível) e dispara liquidação quando é o último a responder")


def teste_accept_sub_order_rejeita_se_nao_estiver_aguardando():
    db = _montar_db()
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "aceito"},
    ])
    sub = master.sub_orders[0]
    try:
        accept_sub_order(sub.gid, db)
        assert False, "deveria ter levantado HTTPException"
    except Exception as e:
        assert "400" in str(e) or "aguardando aceite" in str(e).lower()
    print("OK  - accept_sub_order recusa sub-pedido que já respondeu (accepted_at já preenchido)")


def teste_decline_sub_order_nao_gera_reembolso():
    """O ganho central do plano: recusar não deve chamar Refund.create nenhuma vez."""
    db = _montar_db()
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "pendente"},
        {"restaurant_gid": "01R_B", "total": 15.0, "estado": "pendente"},
    ])
    sub_a, sub_b = master.sub_orders
    with _StripeMocks() as m:
        decline_sub_order(sub_a.gid, {"reason": "sem estoque"}, db)
        # "Cancelado" (não "Recusado") — vocabulário que o app já conhece; o motivo
        # real da recusa fica em declined_at/decline_reason.
        assert sub_a.status == "Cancelado"
        assert sub_a.declined_at is not None
        assert sub_a.decline_reason == "sem estoque"
        assert len(m.refunds) == 0, "recusa não pode gerar reembolso — nunca houve cobrança"

        # Segundo restaurante aceita -> liquida só com ele
        resultado = accept_sub_order(sub_b.gid, db)
        assert resultado["liquidacao"]["acao"] == "captura_solicitada"
        assert len(m.capturas) == 1
        assert m.capturas[0]["amount_to_capture"] == int((15.0 + 2.0) * 100)  # B + taxa de serviço
    print("OK  - recusa nunca chama Refund.create; captura final considera só quem aceitou")


# ── 3. _repassar_para_restaurantes ──────────────────────────────────────────

def teste_repasse_calcula_valor_por_restaurante_conforme_plano_e_comissao():
    db = _montar_db()
    rest_a = _restaurante(db, "01R_A", "Rest A", plan="ESSENCE", use_own_delivery=False,
                           stripe_account_id="acct_teste_a")
    rest_b = _restaurante(db, "01R_B", "Rest B", plan="SMART", use_own_delivery=False,
                           stripe_account_id="acct_teste_b")

    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "aceito"},
        {"restaurant_gid": "01R_B", "total": 15.0, "estado": "aceito"},
    ])
    with _StripeMocks() as m:
        _repassar_para_restaurantes(master, db, {"latest_charge": "ch_teste"})

    assert len(m.transfers) == 2
    # A: ESSENCE 18% sobre 20.0 -> 20 * 0.82 = 16.40
    transfer_a = next(t for t in m.transfers if t["destination"] == rest_a.stripe_account_id)
    assert transfer_a["amount"] == 1640, transfer_a
    # B: SMART 21% sobre 15.0 -> 15 * 0.79 = 11.85
    transfer_b = next(t for t in m.transfers if t["destination"] == rest_b.stripe_account_id)
    assert transfer_b["amount"] == 1185, transfer_b
    print("OK  - repasse aplica a comissão do PLANO de cada restaurante sobre os produtos dele (16.40€ e 11.85€)")


def teste_repasse_inclui_taxa_de_entrega_quando_restaurante_e_proprio():
    db = _montar_db()
    rest = _restaurante(db, "01R_A", "Rest A", plan="ESSENCE", use_own_delivery=True)
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 24.0, "delivery_fee": 4.0, "estado": "aceito"},
    ])
    with _StripeMocks() as m:
        _repassar_para_restaurantes(master, db, {"latest_charge": "ch_teste"})
    # produtos = 24 - 4 = 20; use_own_delivery=True força comissão em 15% (independente
    # do plano, ver get_commission_rate) -> 20*0.85=17.00; + entrega própria 4.0 = 21.00
    assert m.transfers[0]["amount"] == 2100, m.transfers[0]
    print("OK  - repasse soma a taxa de entrega quando o restaurante usa entrega própria "
          "(e a comissão de entrega própria é 15% fixo, não a do plano)")


def teste_repasse_nao_inclui_taxa_de_entrega_quando_plataforma_entrega():
    db = _montar_db()
    rest = _restaurante(db, "01R_A", "Rest A", plan="ESSENCE", use_own_delivery=False)
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 24.0, "delivery_fee": 4.0, "estado": "aceito"},
    ])
    with _StripeMocks() as m:
        _repassar_para_restaurantes(master, db, {"latest_charge": "ch_teste"})
    # produtos = 24 - 4 = 20; comissão 18% -> 16.40; SEM taxa de entrega (fica com a plataforma)
    assert m.transfers[0]["amount"] == 1640
    print("OK  - repasse NÃO inclui taxa de entrega quando a plataforma faz a entrega")


def teste_repasse_e_idempotente_nao_transfere_duas_vezes():
    db = _montar_db()
    _restaurante(db, "01R_A", "Rest A")
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "aceito"},
    ])
    with _StripeMocks() as m:
        _repassar_para_restaurantes(master, db, {"latest_charge": "ch_teste"})
        assert len(m.transfers) == 1
        _repassar_para_restaurantes(master, db, {"latest_charge": "ch_teste"})
        assert len(m.transfers) == 1, "sub já tem stripe_transfer_id — não deveria repassar de novo"
    print("OK  - repasse não transfere 2x para o mesmo sub-pedido (idempotência por stripe_transfer_id)")


def teste_repasse_pula_restaurante_sem_conta_stripe_apta():
    """Rede de segurança adicional: mesmo que o gate do checkout tenha passado antes,
    o repasse não deve quebrar nem transferir para quem não pode receber."""
    db = _montar_db()
    _restaurante(db, "01R_A", "Rest A", stripe_account_id=None, onboarding=False)
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "aceito"},
    ])
    with _StripeMocks() as m:
        _repassar_para_restaurantes(master, db, {"latest_charge": "ch_teste"})
        assert len(m.transfers) == 0
    sub = master.sub_orders[0]
    assert sub.stripe_transfer_id is None
    print("OK  - repasse pula restaurante sem conta Stripe apta, sem quebrar (fica pendente para reconciliação)")


def teste_repasse_ignora_sub_pedido_recusado():
    db = _montar_db()
    _restaurante(db, "01R_A", "Rest A")
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "recusado"},
    ])
    with _StripeMocks() as m:
        _repassar_para_restaurantes(master, db, {"latest_charge": "ch_teste"})
        assert len(m.transfers) == 0
    print("OK  - repasse não transfere para sub-pedido recusado")


# ── 4. _reverter_repasses_do_pedido ──────────────────────────────────────────

def teste_reversao_reverte_valor_transferido():
    db = _montar_db()
    _restaurante(db, "01R_A", "Rest A")
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "aceito"},
    ])
    with _StripeMocks() as m:
        _repassar_para_restaurantes(master, db, {"latest_charge": "ch_teste"})
        valor_transferido = master.sub_orders[0].stripe_transfer_amount

        resultado = _reverter_repasses_do_pedido(master, db)
        assert len(m.reversoes) == 1
        assert m.reversoes[0]["amount"] == int(round(valor_transferido * 100))
        assert master.sub_orders[0].stripe_transfer_reversed == valor_transferido
        assert resultado[0]["sub_order_gid"] == master.sub_orders[0].gid
    print("OK  - reversão reverte exatamente o valor que foi transferido ao restaurante")


def teste_reversao_e_idempotente():
    db = _montar_db()
    _restaurante(db, "01R_A", "Rest A")
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "aceito"},
    ])
    with _StripeMocks() as m:
        _repassar_para_restaurantes(master, db, {"latest_charge": "ch_teste"})
        _reverter_repasses_do_pedido(master, db)
        assert len(m.reversoes) == 1
        _reverter_repasses_do_pedido(master, db)
        assert len(m.reversoes) == 1, "não deveria reverter de novo — já foi revertido por completo"
    print("OK  - reversão não age de novo sobre um repasse já revertido por completo")


def teste_reversao_nao_faz_nada_sem_transfer():
    db = _montar_db()
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "recusado"},
    ])
    with _StripeMocks() as m:
        resultado = _reverter_repasses_do_pedido(master, db)
        assert resultado == []
        assert len(m.reversoes) == 0
    print("OK  - reversão não faz nada quando nenhum repasse foi feito")


# ── 5. Cenário de ponta a ponta (Tabela de testes do plano, cenário #4) ─────

def teste_cenario_completo_3_restaurantes_1_recusa():
    """PLANO_PAGAMENTO_2_ETAPAS.md, Fase 7 — cenário de teste #4: 3 restaurantes,
    1 recusa -> captura parcial, 2 transfers, nada cobrado/repassado do recusado."""
    db = _montar_db()
    _restaurante(db, "01R_A", "Rest A", plan="ESSENCE")
    _restaurante(db, "01R_B", "Rest B", plan="SMART")
    _restaurante(db, "01R_C", "Rest C", plan="ESSENCE")

    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "pendente"},
        {"restaurant_gid": "01R_B", "total": 15.0, "estado": "pendente"},
        {"restaurant_gid": "01R_C", "total": 29.0, "estado": "pendente"},
    ])
    sub_a, sub_b, sub_c = master.sub_orders

    with _StripeMocks() as m:
        accept_sub_order(sub_a.gid, db)
        assert len(m.capturas) == 0, "não liquida enquanto B e C não responderam"

        decline_sub_order(sub_c.gid, {"reason": "fechado"}, db)
        assert len(m.capturas) == 0, "ainda falta B responder"

        resultado = accept_sub_order(sub_b.gid, db)
        assert resultado["liquidacao"]["acao"] == "captura_solicitada"
        assert len(m.capturas) == 1
        assert m.capturas[0]["amount_to_capture"] == int((20.0 + 15.0 + 2.0) * 100)

        # Simula o webhook payment_intent.succeeded chamando o repasse
        _repassar_para_restaurantes(master, db, {"latest_charge": "ch_final"})
        assert len(m.transfers) == 2, "só A e B foram aceitos — C não recebe nada"
        destinos = {t["destination"] for t in m.transfers}
        assert "acct_teste" in destinos  # ambos usam o mesmo stripe_account_id fixture

    assert sub_c.status == "Cancelado"
    assert sub_c.declined_at is not None
    assert sub_c.stripe_transfer_id is None
    print("OK  - cenário completo: 3 restaurantes, 1 recusa -> captura parcial correta, "
          "2 repasses, 0 cobrança/repasse do recusado")


# ── 6. Regressão: arredondamento euro -> cêntimos (bug real de produção) ────
#
# 2026-08-28, produção: "The payment could not be captured because the requested
# capture amount is greater than the amount you can capture for this charge."
# Causa: o checkout autorizava com `int(total * 100)` (TRUNCA) enquanto a captura usa
# `int(round(...))` (ARREDONDA). Erro de representação binária de float (ex.:
# 0.29 * 100 == 28.999999999999996) faz `int()` cortar para 28 cêntimos em vez de 29 —
# a autorização fica 1 cêntimo abaixo do total real, e quando todos os restaurantes
# aceitam, a captura (corretamente arredondada) pede mais do que foi autorizado.
# Corrigido trocando todo `int(x * 100)` de dinheiro por `int(round(x * 100))` no
# checkout (linhas ~454, ~468, ~471) e no reembolso parcial (linha ~906).

def teste_int_trunca_mas_round_acerta_valor_que_ja_quebrou_producao():
    """Documenta o valor exato (0.29€) que expôs o bug: prova que int() e round()
    discordam para ele, e que só round() bate com o cêntimo correto."""
    total = 0.29
    bruto = total * 100
    assert int(bruto) == 28, "premissa do teste mudou — Python não trunca mais aqui"
    assert int(round(bruto)) == 29
    print("OK  - 0.29€ comprova a discrepância: int() trunca para 28 cêntimos, "
          "round() acerta 29 — exatamente o valor que gerou o erro em produção")


def teste_nenhum_int_de_dinheiro_sem_round_em_order_routes():
    """Guarda estático: nenhum `int(<expr> * 100)` (conversão euro->cêntimos) pode
    existir em order_routes.py sem passar por round() — é exatamente esse padrão
    (int() puro truncando em vez de arredondar) que causou o bug de captura acima.
    Se este teste falhar, alguém reintroduziu `int(x * 100)` sem round()."""
    import re
    caminho = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "api", "routes", "order_routes.py")
    with open(caminho, encoding="utf-8") as f:
        codigo = f.read()
    padrao = re.compile(r'int\(\s*(?!round\()[^()]*\*\s*100\s*\)')
    ocorrencias = padrao.findall(codigo)
    assert not ocorrencias, f"int(x * 100) sem round() encontrado: {ocorrencias}"
    print("OK  - nenhuma conversão euro->cêntimos em order_routes.py trunca em vez de arredondar")


def teste_captura_total_bate_com_valor_arredondado_quando_todos_aceitam():
    """Com todos os restaurantes aceitando (captura total), o valor pedido à Stripe
    deve bater exatamente com round(total_do_pedido * 100) — a mesma fórmula que o
    checkout usa hoje para autorizar. Antes da correção, o checkout usava int() puro
    e este valor podia vir 1 cêntimo abaixo do que a captura pede."""
    db = _montar_db()
    # Valores escolhidos para reproduzir o erro de ponto flutuante do bug real.
    # master.total (fixado pela fixture) e a soma recalculada na captura usam a MESMA
    # sequência de somas (subtotais + taxa de serviço), então precisam bater exatamente
    # quando todos os sub-pedidos são aceitos.
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 0.29, "estado": "aceito"},
        {"restaurant_gid": "01R_B", "total": 10.70, "estado": "aceito"},
    ])
    esperado_pela_autorizacao = int(round(master.total * 100))
    with _StripeMocks() as m:
        resultado = _liquidar_pedido_se_todos_responderam(master, db)
        assert resultado["acao"] == "captura_solicitada"
        assert m.capturas[0]["amount_to_capture"] == esperado_pela_autorizacao, (
            m.capturas[0]["amount_to_capture"], esperado_pela_autorizacao
        )
    print("OK  - captura total bate cêntimo a cêntimo com round(total autorizado) mesmo em valor "
          "que expõe erro de ponto flutuante")


# ── 7. Regressão: PUT /status não pode regredir progresso (bug real de produção) ──
#
# 2026-08-28, produção: sub-pedido avançava para "Em preparo" depois do aceite e, em
# seguida, voltava para "Pendente" sozinho. Causa: `update_sub_order_status` gravava
# `sub.status = status_data.status` sem nenhuma checagem de que a transição fazia
# sentido — um reenvio de "Pendente" pelo app (retry duplicado, tela desatualizada)
# depois de já ter mandado "Em preparo" sobrescrevia o progresso de volta.

def teste_put_status_pendente_nao_regride_sub_pedido_ja_avancado():
    db = _montar_db()
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "aceito"},
    ])
    sub = master.sub_orders[0]
    sub.status = "Em preparo"
    db.commit()

    resultado = update_sub_order_status(sub.gid, OrderStatusUpdate(status="Pendente"), db)

    db.refresh(sub)
    assert sub.status == "Em preparo", (
        f"sub-pedido regrediu para '{sub.status}' — regressão do bug de produção "
        "(PUT /status aceitava 'Pendente' como retrocesso sem checar o estado atual)"
    )
    assert resultado["status"] == "Em preparo"
    print("OK  - reenviar status='Pendente' não regride sub-pedido que já avançou para 'Em preparo'")


def teste_put_status_pendente_e_no_op_quando_sub_ainda_esta_pendente():
    """Sanidade: a proteção contra retrocesso não pode quebrar o caso legítimo — um
    app reenviando 'Pendente' enquanto o sub-pedido genuinamente ainda está 'Pendente'
    continua sendo um no-op inofensivo, não um erro."""
    db = _montar_db()
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "aceito"},
    ])
    sub = master.sub_orders[0]
    assert sub.status == "Pendente"

    resultado = update_sub_order_status(sub.gid, OrderStatusUpdate(status="Pendente"), db)

    db.refresh(sub)
    assert sub.status == "Pendente"
    assert resultado["status"] == "Pendente"
    print("OK  - reenviar status='Pendente' continua sendo no-op quando o sub-pedido já está 'Pendente'")


def teste_put_status_avanca_normalmente_de_em_preparo_para_a_caminho():
    """Sanidade: a proteção é só contra 'Pendente' regredindo — avançar normalmente
    entre outros status de progresso continua funcionando sem restrição."""
    db = _montar_db()
    master = _pedido_manual_capture(db, [
        {"restaurant_gid": "01R_A", "total": 20.0, "estado": "aceito"},
    ])
    sub = master.sub_orders[0]
    sub.status = "Em preparo"
    db.commit()

    resultado = update_sub_order_status(sub.gid, OrderStatusUpdate(status="A caminho"), db)

    db.refresh(sub)
    assert sub.status == "A caminho"
    assert resultado["status"] == "A caminho"
    print("OK  - transição normal de progresso (Em preparo -> A caminho) continua funcionando")


if __name__ == "__main__":
    teste_liquidacao_nao_age_com_sub_pedido_pendente()
    teste_liquidacao_cancela_quando_nenhum_aceita()
    teste_liquidacao_captura_produtos_aceitos_mais_taxa_de_servico_integral()
    teste_liquidacao_e_idempotente()
    teste_accept_sub_order_marca_aceito_e_liquida_se_for_o_ultimo()
    teste_accept_sub_order_rejeita_se_nao_estiver_aguardando()
    teste_decline_sub_order_nao_gera_reembolso()
    teste_repasse_calcula_valor_por_restaurante_conforme_plano_e_comissao()
    teste_repasse_inclui_taxa_de_entrega_quando_restaurante_e_proprio()
    teste_repasse_nao_inclui_taxa_de_entrega_quando_plataforma_entrega()
    teste_repasse_e_idempotente_nao_transfere_duas_vezes()
    teste_repasse_pula_restaurante_sem_conta_stripe_apta()
    teste_repasse_ignora_sub_pedido_recusado()
    teste_reversao_reverte_valor_transferido()
    teste_reversao_e_idempotente()
    teste_reversao_nao_faz_nada_sem_transfer()
    teste_cenario_completo_3_restaurantes_1_recusa()
    teste_int_trunca_mas_round_acerta_valor_que_ja_quebrou_producao()
    teste_nenhum_int_de_dinheiro_sem_round_em_order_routes()
    teste_captura_total_bate_com_valor_arredondado_quando_todos_aceitam()
    teste_put_status_pendente_nao_regride_sub_pedido_ja_avancado()
    teste_put_status_pendente_e_no_op_quando_sub_ainda_esta_pendente()
    teste_put_status_avanca_normalmente_de_em_preparo_para_a_caminho()
    print("\nTodos os testes de pagamento em 2 etapas passaram.")
