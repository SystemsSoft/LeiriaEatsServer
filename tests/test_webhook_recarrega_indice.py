"""
Webhook account.updated do Stripe × índice da IA (api/routes/order_routes.py).

Bug (25/09/2026): o webhook marcava o restaurante como apto a receber pagamento no BANCO, mas o índice da IA
(AIService._restaurantes_aptos_pagamento, montado na indexação) só era recarregado no check-status manual.
O dominos concluiu o cadastro e seus produtos seguiram invisíveis no chat até alguém recarregar o índice.

Execução:
    python3 tests/test_webhook_recarrega_indice.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

import stripe
from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from core.sql_models import RestaurantDB
import api.routes.order_routes as rotas
from services.ai_service import AIService


def _preparar(onboarding_inicial: bool):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Sessao = sessionmaker(bind=engine)
    db = Sessao()
    db.add(RestaurantDB(name="Dominos", category="Pizzaria", login="dom", password="x", gid="01DOM",
                        stripe_account_id="acct_dom", stripe_onboarding_completed=onboarding_inicial,
                        status="PENDING"))
    db.commit()
    db.close()
    return Sessao


class _RequestFake:
    async def body(self):
        return b"{}"


def _chamar_webhook(Sessao, completo: bool):
    """Dispara account.updated para a conta do restaurante; devolve as tarefas em segundo plano agendadas."""
    evento = {"type": "account.updated", "data": {"object": {
        "id": "acct_dom", "details_submitted": completo, "charges_enabled": completo, "payouts_enabled": completo}}}
    originais = (rotas.SessionLocal, stripe.Webhook.construct_event)
    rotas.SessionLocal = Sessao
    stripe.Webhook.construct_event = staticmethod(lambda **kw: evento)
    try:
        bg = BackgroundTasks()
        asyncio.run(rotas.stripe_webhook(_RequestFake(), bg, stripe_signature="assinatura"))
        return [t.func.__name__ for t in bg.tasks]
    finally:
        rotas.SessionLocal, stripe.Webhook.construct_event = originais


def teste_restaurante_que_conclui_o_cadastro_recarrega_o_indice():
    Sessao = _preparar(onboarding_inicial=False)
    tarefas = _chamar_webhook(Sessao, completo=True)
    assert tarefas == ["_recarregar_indice_da_ia"], tarefas
    r = Sessao().query(RestaurantDB).filter_by(gid="01DOM").one()
    assert r.stripe_onboarding_completed is True and r.status == "ACTIVE"
    print("OK  - cadastro concluído no Stripe → banco atualizado E recarga do índice agendada")


def teste_evento_repetido_sem_mudanca_nao_recarrega():
    Sessao = _preparar(onboarding_inicial=True)
    assert _chamar_webhook(Sessao, completo=True) == [], "o Stripe reenvia account.updated com frequência"
    print("OK  - evento repetido (nada mudou) não dispara recarga de ~30s")


def teste_cadastro_ainda_incompleto_nao_recarrega():
    Sessao = _preparar(onboarding_inicial=False)
    assert _chamar_webhook(Sessao, completo=False) == []
    print("OK  - cadastro ainda incompleto não recarrega")


def teste_restaurante_que_perde_a_aptidao_tambem_recarrega():
    Sessao = _preparar(onboarding_inicial=True)
    assert _chamar_webhook(Sessao, completo=False) == ["_recarregar_indice_da_ia"]
    print("OK  - restaurante que deixa de estar apto também recarrega (some das sugestões)")


def teste_tarefa_de_recarga_usa_sessao_propria_e_fecha():
    fechadas, recarregou = [], []

    class _SessaoFake:
        def close(self):
            fechadas.append(True)

    originais = (rotas.SessionLocal, AIService.reload_data)
    rotas.SessionLocal = lambda: _SessaoFake()
    AIService.reload_data = classmethod(lambda cls, db: recarregou.append(type(db).__name__))
    try:
        rotas._recarregar_indice_da_ia()
    finally:
        rotas.SessionLocal, AIService.reload_data = originais
    assert recarregou == ["_SessaoFake"] and fechadas == [True]
    print("OK  - a recarga usa uma sessão própria e a fecha")


if __name__ == "__main__":
    teste_restaurante_que_conclui_o_cadastro_recarrega_o_indice()
    teste_evento_repetido_sem_mudanca_nao_recarrega()
    teste_cadastro_ainda_incompleto_nao_recarrega()
    teste_restaurante_que_perde_a_aptidao_tambem_recarrega()
    teste_tarefa_de_recarga_usa_sessao_propria_e_fecha()
    print("\nTodos os testes do webhook × índice da IA passaram.")
