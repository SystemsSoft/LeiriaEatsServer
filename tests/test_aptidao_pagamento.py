"""
Testes do filtro de aptidão de pagamento na IA (PLANO_PAGAMENTO_2_ETAPAS.md, Fase 0).

A IA nunca deve oferecer produto de restaurante sem conta Stripe apta a receber
pagamento — bloquear só no checkout faz o cliente descobrir isso depois de montar o
pedido inteiro pela conversa.

Execução:
    python3 tests/test_aptidao_pagamento.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

import torch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base
from core.sql_models import RestaurantDB, ProductDB
from services.ai_service import AIService
from services.hybrid_ai_service import HybridAIService
from services.session_service import UserSession
from api.routes.order_routes import _validar_restaurantes_aptos_pagamento


# ── 1. AIService._index_data popula _restaurantes_aptos_pagamento ──────────

def _montar_db_com_dois_restaurantes():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    apto = RestaurantDB(
        name="Rest Apto", category="X", login="apto", password="x", gid="01REST_APTO",
        stripe_account_id="acct_teste_apto", stripe_onboarding_completed=True,
    )
    sem_conta = RestaurantDB(
        name="Rest Sem Conta", category="X", login="semconta", password="x", gid="01REST_SEM_CONTA",
        stripe_account_id=None, stripe_onboarding_completed=False,
    )
    onboarding_incompleto = RestaurantDB(
        name="Rest Onboarding Incompleto", category="X", login="incompleto", password="x",
        gid="01REST_ONBOARDING_INCOMPLETO",
        stripe_account_id="acct_teste_incompleto", stripe_onboarding_completed=False,
    )
    db.add_all([apto, sem_conta, onboarding_incompleto])
    db.commit()
    for r in (apto, sem_conta, onboarding_incompleto):
        db.refresh(r)

    produtos = [
        ProductDB(gid="01PROD_APTO", name="Prato Apto", description="", price=10.0,
                   category="X", restaurant_id=apto.id, is_available=True),
        ProductDB(gid="01PROD_SEM_CONTA", name="Prato Sem Conta", description="", price=10.0,
                   category="X", restaurant_id=sem_conta.id, is_available=True),
        ProductDB(gid="01PROD_ONBOARDING_INCOMPLETO", name="Prato Onboarding Incompleto", description="",
                   price=10.0, category="X", restaurant_id=onboarding_incompleto.id, is_available=True),
    ]
    db.add_all(produtos)
    db.commit()
    return db


class _ModeloFake:
    def encode(self, textos, convert_to_tensor=True):
        n = len(textos) if isinstance(textos, list) else 1
        return torch.zeros((n, 4))


def teste_index_data_marca_apenas_restaurante_com_conta_e_onboarding_completo():
    db = _montar_db_com_dois_restaurantes()
    AIService.get_model = classmethod(lambda cls: _ModeloFake())
    AIService.reload_data(db)

    assert AIService._restaurantes_aptos_pagamento == {"01REST_APTO"}
    print("OK  - só entra no conjunto de aptos quem tem stripe_account_id E onboarding completo")


def teste_index_data_exclui_restaurante_sem_conta_stripe():
    db = _montar_db_com_dois_restaurantes()
    AIService.get_model = classmethod(lambda cls: _ModeloFake())
    AIService.reload_data(db)

    assert "01REST_SEM_CONTA" not in AIService._restaurantes_aptos_pagamento
    print("OK  - restaurante sem stripe_account_id fica de fora dos aptos")


def teste_index_data_exclui_onboarding_incompleto():
    """Tem stripe_account_id (a conta Connect foi criada) mas o onboarding não foi
    concluído — não pode receber transferências ainda."""
    db = _montar_db_com_dois_restaurantes()
    AIService.get_model = classmethod(lambda cls: _ModeloFake())
    AIService.reload_data(db)

    assert "01REST_ONBOARDING_INCOMPLETO" not in AIService._restaurantes_aptos_pagamento
    print("OK  - conta Stripe criada mas onboarding incompleto continua fora dos aptos")


# ── 2. HybridAIService._filtrar_pool_por_aptidao_de_pagamento ──────────────

class _ProdutoPoolFake:
    def __init__(self, id, restaurant_gid):
        self.id = id
        self.restaurant_gid = restaurant_gid


def _sessao_vazia():
    return UserSession(session_id="s1")


def teste_filtro_remove_produtos_de_restaurante_inapto():
    AIService._restaurantes_aptos_pagamento = {"01REST_APTO"}
    pool = [
        _ProdutoPoolFake(1, "01REST_APTO"),
        _ProdutoPoolFake(2, "01REST_SEM_CONTA"),
    ]
    resultado = HybridAIService._filtrar_pool_por_aptidao_de_pagamento(pool, _sessao_vazia())
    ids = {p.id for p in resultado}
    assert ids == {1}
    print("OK  - filtro remove do pool produtos de restaurante sem conta Stripe apta")


def teste_filtro_nao_atua_com_cache_vazio():
    """Falha permissiva: se o índice ainda não carregou (cache vazio), o filtro não
    remove nada — o checkout continua protegendo mesmo assim."""
    AIService._restaurantes_aptos_pagamento = set()
    pool = [_ProdutoPoolFake(1, "01QUALQUER")]
    resultado = HybridAIService._filtrar_pool_por_aptidao_de_pagamento(pool, _sessao_vazia())
    assert len(resultado) == 1
    print("OK  - cache vazio não filtra nada (falha permissiva, checkout continua protegendo)")


def teste_filtro_mantem_item_do_carrinho_mesmo_se_restaurante_ficou_inapto():
    """Se a conta Stripe do restaurante for desativada NO MEIO da conversa, o item que
    já está no carrinho precisa continuar visível — senão o cliente fica preso com um
    item que a conversa não consegue mais remover."""
    AIService._restaurantes_aptos_pagamento = {"01REST_APTO"}
    session = _sessao_vazia()
    session.add_to_cart(product_id=99, name="Item já escolhido", price=10.0,
                         restaurant_gid="01REST_FICOU_INAPTO", quantity=1)

    pool = [_ProdutoPoolFake(99, "01REST_FICOU_INAPTO")]
    resultado = HybridAIService._filtrar_pool_por_aptidao_de_pagamento(pool, session)
    assert len(resultado) == 1
    print("OK  - item já no carrinho continua visível mesmo se o restaurante ficou inapto")


def teste_filtro_e_o_de_limite_de_restaurantes_compoem_sem_conflito():
    """Os dois filtros da IA (limite de 3 restaurantes + aptidão de pagamento) rodam em
    sequência no pipeline real — precisam compor sem um desfazer o efeito do outro."""
    AIService._restaurantes_aptos_pagamento = {"01REST_A", "01REST_B"}
    session = _sessao_vazia()
    session.add_to_cart(1, "Produto A", 10.0, "01REST_A", quantity=1)
    session.add_to_cart(2, "Produto B", 10.0, "01REST_B", quantity=1)
    session.add_to_cart(3, "Produto C", 10.0, "01REST_C", quantity=1)  # 3º restaurante = limite

    pool = [
        _ProdutoPoolFake(1, "01REST_A"),
        _ProdutoPoolFake(10, "01REST_A"),      # mesmo restaurante A, produto novo -> deve sobreviver
        _ProdutoPoolFake(20, "01REST_D"),      # restaurante fora do carrinho -> limite remove
        _ProdutoPoolFake(30, "01REST_SEM_STRIPE"),  # restaurante inapto -> aptidão remove
    ]

    pool = HybridAIService._filtrar_pool_por_restaurantes_travados(pool, session)
    pool = HybridAIService._filtrar_pool_por_aptidao_de_pagamento(pool, session)

    ids = {p.id for p in pool}
    assert ids == {1, 10}, f"esperava só produtos do restaurante A (já travado e apto), veio {ids}"
    print("OK  - filtro de limite de restaurantes e de aptidão de pagamento compõem sem conflito")


# ── 3. Gate do checkout — _validar_restaurantes_aptos_pagamento ────────────

class _SubOrderFake:
    def __init__(self, restaurant_gid):
        self.restaurant_gid = restaurant_gid


def teste_checkout_aceita_pedido_com_restaurante_apto():
    db = _montar_db_com_dois_restaurantes()
    sub_orders = [_SubOrderFake("01REST_APTO")]
    erro = _validar_restaurantes_aptos_pagamento(sub_orders, db)
    assert erro is None
    print("OK  - checkout aceita pedido cujo restaurante tem conta Stripe apta")


def teste_checkout_rejeita_restaurante_sem_conta_stripe():
    db = _montar_db_com_dois_restaurantes()
    sub_orders = [_SubOrderFake("01REST_APTO"), _SubOrderFake("01REST_SEM_CONTA")]
    erro = _validar_restaurantes_aptos_pagamento(sub_orders, db)
    assert erro is not None and "Rest Sem Conta" in erro
    print("OK  - checkout rejeita pedido com restaurante sem stripe_account_id, citando o nome")


def teste_checkout_rejeita_onboarding_incompleto():
    db = _montar_db_com_dois_restaurantes()
    sub_orders = [_SubOrderFake("01REST_ONBOARDING_INCOMPLETO")]
    erro = _validar_restaurantes_aptos_pagamento(sub_orders, db)
    assert erro is not None and "Rest Onboarding Incompleto" in erro
    print("OK  - checkout rejeita restaurante com conta Stripe mas onboarding incompleto")


def teste_checkout_rejeita_restaurante_inexistente():
    db = _montar_db_com_dois_restaurantes()
    sub_orders = [_SubOrderFake("01REST_QUE_NAO_EXISTE")]
    erro = _validar_restaurantes_aptos_pagamento(sub_orders, db)
    assert erro is not None and "não encontrado" in erro
    print("OK  - checkout rejeita GID de restaurante que não existe no banco")


def teste_checkout_ignora_sub_orders_sem_gid():
    """Não é papel desta função reclamar de sub_orders vazio ou sem GID —
    isso já é coberto por _validar_sub_orders."""
    db = _montar_db_com_dois_restaurantes()
    erro = _validar_restaurantes_aptos_pagamento([], db)
    assert erro is None
    print("OK  - lista vazia de sub_orders não é problema desta função (papel de _validar_sub_orders)")


if __name__ == "__main__":
    teste_index_data_marca_apenas_restaurante_com_conta_e_onboarding_completo()
    teste_index_data_exclui_restaurante_sem_conta_stripe()
    teste_index_data_exclui_onboarding_incompleto()
    teste_filtro_remove_produtos_de_restaurante_inapto()
    teste_filtro_nao_atua_com_cache_vazio()
    teste_filtro_mantem_item_do_carrinho_mesmo_se_restaurante_ficou_inapto()
    teste_filtro_e_o_de_limite_de_restaurantes_compoem_sem_conflito()
    teste_checkout_aceita_pedido_com_restaurante_apto()
    teste_checkout_rejeita_restaurante_sem_conta_stripe()
    teste_checkout_rejeita_onboarding_incompleto()
    teste_checkout_rejeita_restaurante_inexistente()
    teste_checkout_ignora_sub_orders_sem_gid()
    print("\nTodos os testes de aptidão de pagamento (Fase 0) passaram.")
