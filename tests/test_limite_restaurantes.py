"""
Testes do limite de restaurantes por pedido (PLANO_LIMITE_RESTAURANTES.md).

Execução:
    python3 tests/test_limite_restaurantes.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

from services.session_service import UserSession
from api.routes.order_routes import _validar_sub_orders
from services.hybrid_ai_service import HybridAIService
from services.gemini_sales_service import GeminiSalesAgent


# ── Fase 1.1: UserSession.restaurantes_no_carrinho() ────────────────────────

class _SubOrderFake:
    def __init__(self, restaurant_gid):
        self.restaurant_gid = restaurant_gid


def teste_restaurantes_no_carrinho_vazio():
    session = UserSession(session_id="s1")
    assert session.restaurantes_no_carrinho() == set()
    print("OK  - carrinho vazio não tem restaurantes")


def teste_restaurantes_no_carrinho_conta_distintos():
    session = UserSession(session_id="s2")
    session.add_to_cart(1, "Pizza", 10.0, "01REST_A", quantity=1, restaurant_name="Pizzaria A")
    session.add_to_cart(2, "Sushi", 20.0, "01REST_B", quantity=1, restaurant_name="Sushi B")
    session.add_to_cart(3, "Bebida", 5.0, "01REST_A", quantity=1, restaurant_name="Pizzaria A")
    assert session.restaurantes_no_carrinho() == {"01REST_A", "01REST_B"}
    print("OK  - conta restaurantes distintos, ignorando repetição")


def teste_restaurantes_no_carrinho_ignora_gid_vazio():
    session = UserSession(session_id="s3")
    session.add_to_cart(1, "Item sem restaurante", 10.0, "", quantity=1)
    assert session.restaurantes_no_carrinho() == set()
    print("OK  - GID de restaurante vazio não conta")


def teste_restaurante_libera_ao_remover_ultimo_item():
    """O ciclo central do requisito: remover o último item de um restaurante libera
    o slot automaticamente, porque o estado é derivado — não há lista para dessincronizar."""
    session = UserSession(session_id="s4")
    session.add_to_cart(1, "Pizza", 10.0, "01REST_A", quantity=1)
    session.add_to_cart(2, "Sushi", 20.0, "01REST_B", quantity=1)
    session.add_to_cart(3, "Taco", 15.0, "01REST_C", quantity=1)
    assert len(session.restaurantes_no_carrinho()) == 3

    session.remove_from_cart(3)  # cliente desiste do restaurante C
    assert session.restaurantes_no_carrinho() == {"01REST_A", "01REST_B"}
    print("OK  - remover o último item de um restaurante libera o slot automaticamente")


def teste_nomes_restaurantes_no_carrinho():
    session = UserSession(session_id="s5")
    session.add_to_cart(1, "Pizza", 10.0, "01REST_A", quantity=1, restaurant_name="Pizzaria A")
    session.add_to_cart(2, "Sushi", 20.0, "01REST_B", quantity=1, restaurant_name="Sushi B")
    nomes = session.nomes_restaurantes_no_carrinho()
    assert nomes == {"01REST_A": "Pizzaria A", "01REST_B": "Sushi B"}
    print("OK  - nomes_restaurantes_no_carrinho() devolve {gid: nome} para a IA usar")


def teste_cart_item_restaurant_name_serializa_e_desserializa():
    session = UserSession(session_id="s6")
    session.add_to_cart(1, "Pizza", 10.0, "01REST_A", quantity=1, restaurant_name="Pizzaria A")
    dado = session.cart[0].to_dict()
    assert dado["restaurant_name"] == "Pizzaria A"

    restaurado = UserSession.from_dict(session.to_dict())
    assert restaurado.cart[0].restaurant_name == "Pizzaria A"
    print("OK  - restaurant_name sobrevive a to_dict/from_dict (persistência no Redis)")


def teste_cart_item_from_dict_tolera_sessao_antiga_sem_restaurant_name():
    """Sessões já salvas no Redis antes desta mudança não têm 'restaurant_name' no JSON —
    from_dict não pode quebrar com KeyError."""
    dado_antigo = {
        "product_id": 1, "name": "Pizza", "price": 10.0, "restaurant_gid": "01REST_A",
        "quantity": 1, "serves_people": 1, "category": "",
        # sem "restaurant_name" — formato antigo
    }
    from services.session_service import CartItem
    item = CartItem.from_dict(dado_antigo)
    assert item.restaurant_name == ""
    print("OK  - from_dict tolera sessões antigas sem o campo restaurant_name")


# ── Fase 1.2: gate de checkout (_validar_sub_orders) ────────────────────────

def teste_checkout_rejeita_sub_orders_vazio():
    erro = _validar_sub_orders([], max_restaurantes=3)
    assert erro is not None and "nenhum item" in erro
    print("OK  - checkout rejeita sub_orders vazio com mensagem clara")


def teste_checkout_aceita_ate_o_limite():
    sub_orders = [_SubOrderFake(f"01REST_{i}") for i in range(3)]
    erro = _validar_sub_orders(sub_orders, max_restaurantes=3)
    assert erro is None
    print("OK  - checkout aceita exatamente 3 restaurantes (limite)")


def teste_checkout_rejeita_acima_do_limite():
    sub_orders = [_SubOrderFake(f"01REST_{i}") for i in range(4)]
    erro = _validar_sub_orders(sub_orders, max_restaurantes=3)
    assert erro is not None and "máximo 3" in erro and "recebidos: 4" in erro
    print("OK  - checkout rejeita 4 restaurantes com mensagem específica")


def teste_checkout_conta_restaurantes_distintos_nao_sub_orders():
    """2 sub-pedidos do MESMO restaurante (ex.: itens agrupados de forma diferente pelo
    app) não devem contar como 2 restaurantes."""
    sub_orders = [_SubOrderFake("01REST_A"), _SubOrderFake("01REST_A"), _SubOrderFake("01REST_B")]
    erro = _validar_sub_orders(sub_orders, max_restaurantes=3)
    assert erro is None
    print("OK  - conta restaurantes distintos, não quantidade de sub_orders")


def teste_checkout_limite_configuravel():
    sub_orders = [_SubOrderFake(f"01REST_{i}") for i in range(2)]
    assert _validar_sub_orders(sub_orders, max_restaurantes=1) is not None
    assert _validar_sub_orders(sub_orders, max_restaurantes=2) is None
    print("OK  - limite é parametrizável (não hardcoded em 3 dentro da função)")


# ── Fase 2: HybridAIService._bloqueado_por_limite_restaurantes ─────────────

def _sessao_com_n_restaurantes(n):
    session = UserSession(session_id="sX")
    for i in range(n):
        session.add_to_cart(i, f"Produto {i}", 10.0, f"01REST_{i}", quantity=1,
                             restaurant_name=f"Restaurante {i}")
    return session


def teste_bloqueio_recusa_restaurante_novo_no_limite():
    session = _sessao_com_n_restaurantes(3)
    bloqueado = HybridAIService._bloqueado_por_limite_restaurantes(session, "01REST_NOVO", delta=1)
    assert bloqueado is True
    print("OK  - bloqueia adição de restaurante novo quando o limite (3) já foi atingido")


def teste_bloqueio_permite_mais_itens_de_restaurante_ja_escolhido():
    session = _sessao_com_n_restaurantes(3)
    bloqueado = HybridAIService._bloqueado_por_limite_restaurantes(session, "01REST_0", delta=1)
    assert bloqueado is False
    print("OK  - permite adicionar mais itens de um restaurante JÁ no carrinho, mesmo no limite")


def teste_bloqueio_permite_remocao_mesmo_no_limite():
    session = _sessao_com_n_restaurantes(3)
    bloqueado = HybridAIService._bloqueado_por_limite_restaurantes(session, "01REST_NOVO", delta=-1)
    assert bloqueado is False
    print("OK  - nunca bloqueia remoção (delta<=0), mesmo com um GID de restaurante novo")


def teste_bloqueio_nao_dispara_abaixo_do_limite():
    session = _sessao_com_n_restaurantes(2)
    bloqueado = HybridAIService._bloqueado_por_limite_restaurantes(session, "01REST_NOVO", delta=1)
    assert bloqueado is False
    print("OK  - não bloqueia 3º restaurante novo quando ainda há espaço (2/3)")


# ── Fase 3.1: HybridAIService._filtrar_pool_por_restaurantes_travados ──────

class _ProdutoPoolFake:
    def __init__(self, id, restaurant_gid):
        self.id = id
        self.restaurant_gid = restaurant_gid


def teste_filtro_pool_nao_filtra_abaixo_do_limite():
    session = _sessao_com_n_restaurantes(2)
    pool = [_ProdutoPoolFake(100, "01REST_OUTRO"), _ProdutoPoolFake(101, "01REST_0")]
    resultado = HybridAIService._filtrar_pool_por_restaurantes_travados(pool, session)
    assert len(resultado) == 2
    print("OK  - pool não é filtrado quando o limite ainda não foi atingido")


def teste_filtro_pool_restringe_aos_restaurantes_travados():
    session = _sessao_com_n_restaurantes(3)  # 01REST_0, 01REST_1, 01REST_2
    pool = [
        _ProdutoPoolFake(100, "01REST_0"),   # de um restaurante travado -> fica
        _ProdutoPoolFake(101, "01REST_FORA"),  # de fora -> removido
    ]
    resultado = HybridAIService._filtrar_pool_por_restaurantes_travados(pool, session)
    ids_resultado = {p.id for p in resultado}
    assert ids_resultado == {100}
    print("OK  - no limite, pool mantém só produtos dos restaurantes já escolhidos")


def teste_filtro_pool_mantem_item_do_carrinho_mesmo_com_gid_divergente():
    """Mesmo que o restaurant_gid do produto no pool não bata com o do carrinho (dado
    inconsistente), o item continua visível se o ID já está no carrinho — senão o
    cliente ficaria preso com um item que a conversa não consegue mais remover."""
    session = _sessao_com_n_restaurantes(3)
    produto_no_carrinho_com_gid_diferente = _ProdutoPoolFake(0, "01REST_GID_DIVERGENTE")
    pool = [produto_no_carrinho_com_gid_diferente]
    resultado = HybridAIService._filtrar_pool_por_restaurantes_travados(pool, session)
    assert len(resultado) == 1
    print("OK  - item já no carrinho nunca é removido do pool, mesmo com GID divergente")


# ── Fase 3.2: seção de restaurantes no prompt ───────────────────────────────

def teste_prompt_sem_secao_de_restaurantes_quando_carrinho_vazio():
    prompt = GeminiSalesAgent._build_prompt(
        "oi", products=[], context={"restaurantes_no_pedido": {}},
    )
    assert "RESTAURANTES NO PEDIDO" not in prompt
    print("OK  - prompt não mostra a seção de restaurantes com carrinho vazio")


def teste_prompt_mostra_restaurantes_sem_limite_atingido():
    contexto = {
        "restaurantes_no_pedido": {"01A": "Pizzaria A", "01B": "Sushi B"},
        "max_restaurantes_por_pedido": 3,
    }
    prompt = GeminiSalesAgent._build_prompt("quero mais um item", products=[], context=contexto)
    assert "RESTAURANTES NO PEDIDO (2/3)" in prompt
    assert "LIMITE ATINGIDO" not in prompt
    assert "Pizzaria A" in prompt and "Sushi B" in prompt
    print("OK  - prompt mostra contagem de restaurantes sem marcar limite atingido")


def teste_prompt_marca_limite_atingido():
    contexto = {
        "restaurantes_no_pedido": {"01A": "Pizzaria A", "01B": "Sushi B", "01C": "Café C"},
        "max_restaurantes_por_pedido": 3,
    }
    prompt = GeminiSalesAgent._build_prompt("quero algo de outro lugar", products=[], context=contexto)
    assert "RESTAURANTES NO PEDIDO (3/3 — LIMITE ATINGIDO)" in prompt
    print("OK  - prompt marca LIMITE ATINGIDO quando restaurantes_no_pedido == max")


# ── Fase 4.1: ciclo completo de destravamento ───────────────────────────────

def teste_ciclo_completo_destravamento():
    """O requisito original, ponta a ponta: 3 restaurantes no carrinho -> 4º é
    recusado -> cliente desiste de um dos 3 -> 4º passa a ser aceito. Tudo isso sem
    nenhum código de "liberação" — é consequência direta do estado ser derivado."""
    session = _sessao_com_n_restaurantes(3)  # 01REST_0, 01REST_1, 01REST_2

    # 1. Um 4º restaurante é recusado.
    assert HybridAIService._bloqueado_por_limite_restaurantes(session, "01REST_NOVO", delta=1) is True

    # 2. O pool, nesse momento, também estaria restrito aos 3 atuais.
    pool = [_ProdutoPoolFake(200, "01REST_NOVO"), _ProdutoPoolFake(0, "01REST_0")]
    pool_filtrado = HybridAIService._filtrar_pool_por_restaurantes_travados(pool, session)
    assert {p.id for p in pool_filtrado} == {0}

    # 3. Cliente desiste do restaurante 2 (remove o único item de lá).
    session.remove_from_cart(2)
    assert len(session.restaurantes_no_carrinho()) == 2

    # 4. Agora o restaurante novo é aceito.
    assert HybridAIService._bloqueado_por_limite_restaurantes(session, "01REST_NOVO", delta=1) is False

    # 5. E o pool volta a mostrar todo mundo.
    pool_filtrado_apos = HybridAIService._filtrar_pool_por_restaurantes_travados(pool, session)
    assert len(pool_filtrado_apos) == 2
    print("OK  - ciclo completo: 3 restaurantes -> 4º recusado -> remove um -> 4º aceito, sem código de liberação")


# ── Fase 4.2: telemetria ────────────────────────────────────────────────────

def teste_telemetria_registra_campos_de_restaurante():
    import io
    import json
    import logging
    from services import telemetry

    buffer = io.StringIO()
    handler_temp = logging.StreamHandler(buffer)
    telemetry._logger.addHandler(handler_temp)
    try:
        telemetry.registrar_turno(
            session_id="sessao-tel", restaurant_gid="01REST_0", modelo="teste",
            ms_e5=1.0, ms_pool=1.0, ms_ttft=1.0, ms_total=1.0, pool_size=1,
            tokens_prompt_estimado=10, tags_emitidas=0, tags_aplicadas=0,
            restaurantes_no_carrinho=3, limite_restaurantes_atingido=True,
        )
    finally:
        telemetry._logger.removeHandler(handler_temp)

    evento = json.loads(buffer.getvalue().strip().splitlines()[-1])
    assert evento["restaurantes_no_carrinho"] == 3
    assert evento["limite_restaurantes_atingido"] is True
    print("OK  - registrar_turno grava restaurantes_no_carrinho e limite_restaurantes_atingido")


if __name__ == "__main__":
    teste_restaurantes_no_carrinho_vazio()
    teste_restaurantes_no_carrinho_conta_distintos()
    teste_restaurantes_no_carrinho_ignora_gid_vazio()
    teste_restaurante_libera_ao_remover_ultimo_item()
    teste_nomes_restaurantes_no_carrinho()
    teste_cart_item_restaurant_name_serializa_e_desserializa()
    teste_cart_item_from_dict_tolera_sessao_antiga_sem_restaurant_name()
    teste_checkout_rejeita_sub_orders_vazio()
    teste_checkout_aceita_ate_o_limite()
    teste_checkout_rejeita_acima_do_limite()
    teste_checkout_conta_restaurantes_distintos_nao_sub_orders()
    teste_checkout_limite_configuravel()
    teste_bloqueio_recusa_restaurante_novo_no_limite()
    teste_bloqueio_permite_mais_itens_de_restaurante_ja_escolhido()
    teste_bloqueio_permite_remocao_mesmo_no_limite()
    teste_bloqueio_nao_dispara_abaixo_do_limite()
    teste_filtro_pool_nao_filtra_abaixo_do_limite()
    teste_filtro_pool_restringe_aos_restaurantes_travados()
    teste_filtro_pool_mantem_item_do_carrinho_mesmo_com_gid_divergente()
    teste_prompt_sem_secao_de_restaurantes_quando_carrinho_vazio()
    teste_prompt_mostra_restaurantes_sem_limite_atingido()
    teste_prompt_marca_limite_atingido()
    teste_ciclo_completo_destravamento()
    teste_telemetria_registra_campos_de_restaurante()
    print("\nTodos os testes de limite de restaurantes (Fases 1-4) passaram.")
