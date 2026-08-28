# Arquivo: services/payment_reconciliation_service.py
import asyncio
import logging
import os
import stripe
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from core.database import SessionLocal
from core.sql_models import OrderDB, SubOrderDB
from core import config

logger = logging.getLogger("payment_reconciliation")
stripe.api_key = config.settings.STRIPE_API_KEY

POLL_INTERVAL_SECONDS = 30  # Verifica a cada 30 segundos
MAX_ORDER_AGE_MINUTES = 60  # Apenas reconcilia pedidos criados na última hora

# PLANO_PAGAMENTO_2_ETAPAS.md, Fase 6.2 — prazo de resposta do restaurante. Um
# restaurante que não aceitar nem recusar dentro deste tempo tem o sub-pedido recusado
# automaticamente. Valor sugerido no plano (15 min), configurável — a validar com a
# operação, não é uma decisão técnica.
PRAZO_ACEITE_MINUTOS = int(os.getenv("PRAZO_ACEITE_MINUTOS", "15"))


async def payment_reconciliation_worker():
    """
    Worker em background que verifica se pedidos PENDING_PAYMENT foram pagos na Stripe.
    Serve como redundância caso o Webhook falhe.

    PLANO_PAGAMENTO_2_ETAPAS.md, Fase 6.3 — estendido com três rotinas adicionais para
    o fluxo de captura manual: expirar sub-pedidos sem resposta do restaurante, retentar
    repasses que não completaram (webhook perdido), e o cinto de segurança que cancela
    QUALQUER pedido AUTHORIZED preso além do prazo, independente do estado dos
    sub-pedidos — cobre o caso do fluxo normal ter falhado por algum outro motivo
    (worker fora do ar, bug, pedido fora da busca das outras rotinas).

    O prazo do cinto de segurança (PRAZO_SEGURANCA_AUTORIZACAO_MINUTOS, default 60min em
    core/config.py) foi fixado em 1h por decisão consciente: está bem acima do fluxo
    normal de aceite (15min) e bem abaixo de qualquer janela real de expiração de
    autorização do Stripe (medida em dias) — mas esse número exato do Stripe ainda não
    foi confirmado na documentação oficial. Reavaliar a margem quando for. Ver
    PLANO_PAGAMENTO_2_ETAPAS.md, Fase 6.1.
    """
    logger.info("🟢 Payment reconciliation worker iniciado.")
    print("🟢 Payment reconciliation worker iniciado.")

    while True:
        try:
            await _reconcile_pending_payments()
        except Exception as e:
            logger.error(f"❌ Erro no reconciliation worker: {e}")
            print(f"❌ [RECONCILIATION] Erro: {e}")

        try:
            await _expirar_sub_pedidos_sem_resposta()
        except Exception as e:
            logger.error(f"❌ Erro ao expirar sub-pedidos sem resposta: {e}")
            print(f"❌ [RECONCILIATION] Erro (expiração de aceite): {e}")

        try:
            await _cancelar_autorizacoes_presas()
        except Exception as e:
            logger.error(f"❌ Erro no cinto de segurança de autorizações presas: {e}")
            print(f"❌ [RECONCILIATION] Erro (cinto de segurança): {e}")

        try:
            await _repassar_pendentes()
        except Exception as e:
            logger.error(f"❌ Erro ao retentar repasses pendentes: {e}")
            print(f"❌ [RECONCILIATION] Erro (repasse pendente): {e}")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _reconcile_pending_payments():
    """
    Busca pedidos pendentes e valida status na Stripe.

    Ramifica em dois caminhos por `payment_flow`: MANUAL_CAPTURE nunca deve ser movido
    para "Pendente" só porque o PaymentIntent tem status "succeeded" sem que a captura
    tenha de fato sido solicitada por `_liquidar_pedido_se_todos_responderam` — aqui
    tratamos apenas a redundância de um webhook `amount_capturable_updated` perdido
    (autorização confirmada, mas o pedido ainda em PENDING_PAYMENT no banco).
    """
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=MAX_ORDER_AGE_MINUTES)

        pending_orders = db.query(OrderDB).filter(
            OrderDB.status == "PENDING_PAYMENT",
            OrderDB.payment_intent_id.isnot(None),
            OrderDB.created_at >= cutoff
        ).all()

        if not pending_orders:
            return

        for order in pending_orders:
            try:
                pi = stripe.PaymentIntent.retrieve(order.payment_intent_id)

                if order.payment_flow == "MANUAL_CAPTURE":
                    if pi.status == "requires_capture":
                        # Redundância do webhook payment_intent.amount_capturable_updated.
                        # "Pendente" (não "AGUARDANDO_ACEITE") — apps legadas só reconhecem
                        # o vocabulário existente; o estado real de espera de aceite vive em
                        # payment_status/accepted_at/declined_at.
                        order.payment_status = "AUTHORIZED"
                        order.status = "Pendente"
                        if not order.authorization_expires_at:
                            # Mesma margem de segurança da Fase 6.1 — ver core/config.py,
                            # PRAZO_SEGURANCA_AUTORIZACAO_MINUTOS.
                            order.authorization_expires_at = (
                                datetime.now(timezone.utc)
                                + timedelta(minutes=config.settings.PRAZO_SEGURANCA_AUTORIZACAO_MINUTOS)
                            )
                        for sub in order.sub_orders:
                            # Sub-pedido nasce com status="PENDING_PAYMENT" (nunca None —
                            # ver criação em order_routes.py); é esse o valor que precisa
                            # avançar para "Pendente" quando a autorização é confirmada.
                            # BUG real de produção (2026-08-28): esta condição checava
                            # `sub.status is None`, que nunca é verdade, então o sub-pedido
                            # ficava travado em "PENDING_PAYMENT" para sempre quando era
                            # ESTA rotina (redundância) — e não o webhook
                            # amount_capturable_updated — quem confirmava a autorização.
                            if sub.status in ("PENDING_PAYMENT", None):
                                sub.status = "Pendente"
                        db.commit()
                        print(f"💰 [RECONCILIATION] Pedido #{order.id} autorizado (webhook perdido, "
                              f"corrigido pelo worker) — aguardando aceite dos restaurantes")
                    elif pi.status == "canceled":
                        order.payment_status = "CANCELED"
                        order.status = "Cancelado"
                        for sub in order.sub_orders:
                            sub.status = "Cancelado"
                        db.commit()
                        print(f"🚫 [RECONCILIATION] Pedido #{order.id} (autorização) cancelado na Stripe. Sincronizado.")
                    # pi.status == "succeeded" para MANUAL_CAPTURE só deve acontecer depois
                    # de _liquidar_pedido_se_todos_responderam chamar capture() — não há
                    # nada a fazer aqui além do que o webhook payment_intent.succeeded já
                    # trata (inclusive o repasse, coberto por _repassar_pendentes abaixo).
                else:
                    # Caminho AUTO_CAPTURE / legado — inalterado.
                    if pi.status == "succeeded":
                        print(f"💰 [RECONCILIATION] Sucesso detectado para Pedido #{order.id} (PI: {order.payment_intent_id})")
                        order.status = "Pendente"
                        for sub in order.sub_orders:
                            sub.status = "Pendente"
                        db.commit()
                        print(f"✅ [RECONCILIATION] Pedido #{order.id} sincronizado para 'Pendente'.")
                    elif pi.status == "canceled":
                        order.status = "Cancelado"
                        for sub in order.sub_orders:
                            sub.status = "Cancelado"
                        db.commit()
                        print(f"🚫 [RECONCILIATION] Pedido #{order.id} cancelado na Stripe. Sincronizado.")

            except stripe.error.StripeError as e:
                logger.warning(f"⚠️ Erro ao consultar Stripe para Pedido #{order.id}: {e}")
            except Exception as e:
                logger.error(f"❌ Erro ao processar Pedido #{order.id}: {e}")

    finally:
        db.close()


async def _expirar_sub_pedidos_sem_resposta():
    """
    PLANO_PAGAMENTO_2_ETAPAS.md, Fase 6.2 — recusa automaticamente sub-pedidos sem
    resposta (nem aceitos, nem recusados) há mais de PRAZO_ACEITE_MINUTOS, e liquida o
    pedido com o que sobrou (captura só os aceitos, ou cancela se nenhum aceitou).

    O estado "aguardando aceite" NÃO é um valor de `status` (esse campo só usa
    vocabulário que apps legadas já conhecem, ex. "Pendente"/"Cancelado") — é
    `accepted_at is None and declined_at is None`, com o pedido AUTHORIZED.

    Usa `OrderDB.created_at` como aproximação do início da espera (o pedido não tem um
    campo separado de "autorizado em"): a diferença entre criação do checkout e
    confirmação da autorização é tipicamente de segundos, desprezível contra uma janela
    de minutos.
    """
    # Import local para evitar import circular (order_routes importa deste módulo
    # indiretamente via main.py; este módulo não deve importar order_routes no topo).
    from api.routes.order_routes import _liquidar_pedido_se_todos_responderam

    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=PRAZO_ACEITE_MINUTOS)

        expirados = (
            db.query(SubOrderDB)
            .join(OrderDB, OrderDB.gid == SubOrderDB.master_order_gid)
            .filter(
                SubOrderDB.accepted_at.is_(None),
                SubOrderDB.declined_at.is_(None),
                OrderDB.payment_flow == "MANUAL_CAPTURE",
                OrderDB.payment_status == "AUTHORIZED",
                OrderDB.created_at <= cutoff,
            )
            .all()
        )

        if not expirados:
            return

        masters_afetados = {}
        for sub in expirados:
            sub.status = "Cancelado"
            sub.declined_at = datetime.now(timezone.utc)
            sub.decline_reason = f"Sem resposta do restaurante em {PRAZO_ACEITE_MINUTOS} minutos (recusa automática)"
            print(f"⏱️ [RECONCILIATION] Sub-pedido {sub.gid} recusado automaticamente (prazo de "
                  f"{PRAZO_ACEITE_MINUTOS}min esgotado)")
            masters_afetados[sub.master_order_gid] = sub.master_order
        db.commit()

        for master in masters_afetados.values():
            if master:
                resultado = _liquidar_pedido_se_todos_responderam(master, db)
                print(f"ℹ️ [RECONCILIATION] Liquidação do pedido #{master.id} após expiração: {resultado}")

    finally:
        db.close()


async def _cancelar_autorizacoes_presas():
    """
    PLANO_PAGAMENTO_2_ETAPAS.md, Fase 6.1 — cinto de segurança final. Diferente de
    `_expirar_sub_pedidos_sem_resposta` (que age SUB-PEDIDO por sub-pedido, dentro do
    fluxo normal de aceite/recusa), esta rotina age no PEDIDO inteiro e não depende de
    nenhuma lógica de aceite: se `authorization_expires_at` já passou e o pedido ainda
    está `AUTHORIZED`, cancela — não importa em que estado os sub-pedidos estão, nem
    por que o fluxo normal não resolveu sozinho.

    Idempotente: `PaymentIntent.cancel` com idempotency_key, e o filtro por
    `payment_status == "AUTHORIZED"` já exclui pedidos que este loop já cancelou numa
    rodada anterior (na próxima consulta o status já não bate mais).
    """
    db = SessionLocal()
    try:
        agora = datetime.now(timezone.utc)
        presos = db.query(OrderDB).filter(
            OrderDB.payment_flow == "MANUAL_CAPTURE",
            OrderDB.payment_status == "AUTHORIZED",
            OrderDB.authorization_expires_at.isnot(None),
            OrderDB.authorization_expires_at <= agora,
        ).all()

        for order in presos:
            try:
                stripe.PaymentIntent.cancel(
                    order.payment_intent_id,
                    idempotency_key=f"cancel_order_{order.gid}",
                )
            except stripe.error.InvalidRequestError as e:
                # Pode já ter sido capturado/cancelado por outro caminho entre a busca
                # e esta chamada — não impede a sincronização do estado local abaixo.
                logger.warning(f"⚠️ [Cinto de segurança] PaymentIntent.cancel falhou para "
                                f"pedido #{order.id}: {e}")

            order.payment_status = "CANCELED"
            order.status = "Cancelado"
            for sub in order.sub_orders:
                # Cinto de segurança age no PEDIDO inteiro: cancela todo sub-pedido
                # ainda sem resposta, independente de qual seja o `status` atual dele.
                if sub.declined_at is None and sub.accepted_at is None:
                    sub.status = "Cancelado"
                    sub.declined_at = agora
                    sub.decline_reason = sub.decline_reason or "Autorização cancelada pelo cinto de segurança (prazo esgotado)"
            db.commit()
            print(f"🛑 [Cinto de Segurança] Pedido #{order.id}: AUTHORIZED preso além de "
                  f"{config.settings.PRAZO_SEGURANCA_AUTORIZACAO_MINUTOS}min — autorização "
                  f"cancelada, sem cobrança ao cliente")

    finally:
        db.close()


async def _repassar_pendentes():
    """
    PLANO_PAGAMENTO_2_ETAPAS.md, Fase 6.3 — rede de segurança do repasse: pedidos já
    CAPTURED com sub-pedido aceito (accepted_at preenchido) mas sem stripe_transfer_id (webhook
    payment_intent.succeeded perdido, ou o restaurante concluiu o onboarding do Stripe
    DEPOIS da tentativa original — Fase 0 already o teria bloqueado no checkout, mas
    aceite pode acontecer minutos depois). Idempotente pela mesma idempotency_key usada
    no caminho principal.
    """
    from api.routes.order_routes import _repassar_para_restaurantes

    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=MAX_ORDER_AGE_MINUTES * 24)  # até 24h atrás

        candidatos = (
            db.query(OrderDB)
            .filter(
                OrderDB.payment_flow == "MANUAL_CAPTURE",
                OrderDB.payment_status == "CAPTURED",
                OrderDB.payment_intent_id.isnot(None),
                OrderDB.created_at >= cutoff,
            )
            .all()
        )

        for master in candidatos:
            pendente = any(s.accepted_at is not None and not s.stripe_transfer_id for s in master.sub_orders)
            if not pendente:
                continue
            try:
                pi = stripe.PaymentIntent.retrieve(master.payment_intent_id)
                pi_dict = pi.to_dict() if hasattr(pi, "to_dict") else dict(pi)
                print(f"🔁 [RECONCILIATION] Repasse pendente detectado para pedido #{master.id} — retentando")
                _repassar_para_restaurantes(master, db, pi_dict)
            except stripe.error.StripeError as e:
                logger.warning(f"⚠️ Erro ao retentar repasse do pedido #{master.id}: {e}")

    finally:
        db.close()
