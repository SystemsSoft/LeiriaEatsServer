"""
Testes de function calling (F3 do PLANO_EXECUCAO_IA.md).

IMPORTANTE: a GEMINI_API_KEY deste projeto estava com créditos esgotados (429
RESOURCE_EXHAUSTED, confirmado durante o desenvolvimento) — estes testes usam um dublê
do cliente do SDK google-genai, com o formato de resposta validado por introspecção do
SDK realmente instalado (google-genai 2.17.0), não por suposição. O round-trip contra a
API viva ainda precisa ser confirmado antes de ligar IA_FUNCTION_CALLING em produção.

Cobre:
  1. GeminiSalesAgent._TOOLS tem o formato esperado pelo SDK instalado.
  2. HybridAIService._executar_ferramenta valida GID, teto de quantidade e disponibilidade
     (o "executor" do F3.2 — é aqui que a ação de fato acontece, não no texto do modelo).
  3. GeminiSalesAgent.generate_response_with_tools completa o loop
     function_call → executor → function_response → texto final.
  4. process_sales_chat com IA_FUNCTION_CALLING=true usa o novo caminho; com a variável
     ausente/false, o caminho de tags original continua intacto (regressão zero).

Execução:
    python3 tests/test_function_calling.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

from services.gemini_sales_service import GeminiSalesAgent
from services.hybrid_ai_service import HybridAIService
from services.session_service import UserSession


# ── 1. Formato das ferramentas ──────────────────────────────────────────────

def teste_ferramentas_tem_schema_esperado():
    tools = GeminiSalesAgent._TOOLS
    assert len(tools) == 1
    nomes = {fd.name for fd in tools[0].function_declarations}
    assert nomes == {"adicionar_ao_carrinho", "mostrar_sacola", "sugerir_produtos"}

    fd_add = next(fd for fd in tools[0].function_declarations if fd.name == "adicionar_ao_carrinho")
    schema = fd_add.parameters_json_schema
    assert schema["required"] == ["product_gid", "delta_quantidade"]
    assert schema["properties"]["delta_quantidade"]["type"] == "integer"

    fd_sugerir = next(fd for fd in tools[0].function_declarations if fd.name == "sugerir_produtos")
    assert fd_sugerir.parameters_json_schema["required"] == ["gids"]
    assert fd_sugerir.parameters_json_schema["properties"]["gids"]["type"] == "array"
    print("OK  - _TOOLS tem o schema esperado (3 ferramentas, campos obrigatórios corretos)")


# ── 2. Executor validado (F3.2) ─────────────────────────────────────────────

def _sessao_com_carrinho(itens):
    s = UserSession(session_id="s1")
    for pid, qty in itens:
        s.add_to_cart(product_id=pid, name=f"Produto {pid}", price=10.0,
                       restaurant_gid="01REST1", quantity=qty)
    return s


def teste_executor_rejeita_gid_fora_do_pool():
    session = _sessao_com_carrinho([])
    pool = [{"id": 1, "gid": "01PROD1", "name": "Pizza", "price": 10.0, "restaurant_gid": "01REST1", "is_available": True}]
    estado = {"carrinho_mudou": False, "show_cart": False}

    resultado = HybridAIService._executar_ferramenta(
        "adicionar_ao_carrinho", {"product_gid": "GID_INEXISTENTE", "delta_quantidade": 1},
        session, pool, estado,
    )
    assert resultado["ok"] is False
    assert resultado["erro"] == "GID_FORA_DO_CATALOGO"
    assert len(session.cart) == 0
    assert estado["carrinho_mudou"] is False
    print("OK  - executor rejeita GID fora do pool sem tocar no carrinho")


def teste_executor_aplica_teto_de_quantidade():
    session = _sessao_com_carrinho([])
    pool = [{"id": 1, "gid": "01PROD1", "name": "Pizza", "price": 10.0, "restaurant_gid": "01REST1", "is_available": True}]
    estado = {"carrinho_mudou": False, "show_cart": False}

    resultado = HybridAIService._executar_ferramenta(
        "adicionar_ao_carrinho", {"product_gid": "01PROD1", "delta_quantidade": 999},
        session, pool, estado,
    )
    assert resultado["ok"] is True
    assert resultado["quantidade_final"] == HybridAIService.MAX_QTD_ITEM_FUNCTION_CALLING
    assert session.cart[0].quantity == HybridAIService.MAX_QTD_ITEM_FUNCTION_CALLING
    print(f"OK  - executor aplica teto de {HybridAIService.MAX_QTD_ITEM_FUNCTION_CALLING} itens mesmo com delta absurdo (999)")


def teste_executor_delta_e_incremental_nao_absoluto():
    """Regressão do comportamento documentado: 'já tem 1, quer 3 no total' -> delta=2."""
    session = _sessao_com_carrinho([(1, 1)])
    pool = [{"id": 1, "gid": "01PROD1", "name": "Pizza", "price": 10.0, "restaurant_gid": "01REST1", "is_available": True}]
    estado = {"carrinho_mudou": False, "show_cart": False}

    resultado = HybridAIService._executar_ferramenta(
        "adicionar_ao_carrinho", {"product_gid": "01PROD1", "delta_quantidade": 2},
        session, pool, estado,
    )
    assert resultado["ok"] is True
    assert resultado["quantidade_final"] == 3
    assert session.cart[0].quantity == 3
    print("OK  - delta_quantidade é somado ao existente (incremental), não substitui")


def teste_executor_rejeita_produto_indisponivel():
    session = _sessao_com_carrinho([])
    pool = [{"id": 1, "gid": "01PROD1", "name": "Pizza", "price": 10.0, "restaurant_gid": "01REST1", "is_available": False}]
    estado = {"carrinho_mudou": False, "show_cart": False}

    resultado = HybridAIService._executar_ferramenta(
        "adicionar_ao_carrinho", {"product_gid": "01PROD1", "delta_quantidade": 1},
        session, pool, estado,
    )
    assert resultado["ok"] is False
    assert resultado["erro"] == "PRODUTO_INDISPONIVEL"
    print("OK  - executor rejeita produto marcado como indisponível")


def teste_executor_mostrar_sacola_seta_estado():
    estado = {"carrinho_mudou": False, "show_cart": False}
    resultado = HybridAIService._executar_ferramenta("mostrar_sacola", {}, _sessao_com_carrinho([]), [], estado)
    assert resultado["ok"] is True
    assert estado["show_cart"] is True
    print("OK  - ferramenta mostrar_sacola sinaliza estado corretamente")


def teste_executor_sugerir_produtos_filtra_gids_fora_do_pool():
    """F4.2: substitui _filter_mentioned_products — o modelo declara os GIDs, o
    executor só valida contra o pool (não confia cegamente no que o modelo mandou)."""
    pool = [
        {"id": 1, "gid": "01PROD1", "name": "Pizza", "price": 10.0, "restaurant_gid": "01REST1", "is_available": True},
        {"id": 2, "gid": "01PROD2", "name": "Sushi", "price": 20.0, "restaurant_gid": "01REST1", "is_available": True},
    ]
    estado = {"carrinho_mudou": False, "show_cart": False}
    resultado = HybridAIService._executar_ferramenta(
        "sugerir_produtos", {"gids": ["01PROD1", "GID_INVENTADO", "01PROD2"]},
        _sessao_com_carrinho([]), pool, estado,
    )
    assert resultado["ok"] is True
    assert resultado["aviso"] == "ALGUNS_GIDS_FORA_DO_CATALOGO_FORAM_IGNORADOS"
    assert estado["gids_sugeridos"] == ["01PROD1", "01PROD2"]
    print("OK  - sugerir_produtos aceita GIDs válidos e descarta os que não existem no pool")


# ── 3. Loop completo com dublê do SDK ───────────────────────────────────────

class _FuncCallFake:
    def __init__(self, name, args):
        self.name = name
        self.args = args


class _RespostaFake:
    """Simula google.genai.types.GenerateContentResponse o suficiente para o loop
    em GeminiSalesAgent.generate_response_with_tools."""
    def __init__(self, texto=None, function_calls=None, content_echo=None):
        self.text = texto
        self.function_calls = function_calls or []
        self.candidates = [_CandidatoFake(content_echo)]


class _CandidatoFake:
    def __init__(self, content):
        self.content = content or "conteudo-fake-do-modelo"


class _ModelsFake:
    def __init__(self, respostas):
        self._respostas = list(respostas)
        self.chamadas = 0

    def generate_content(self, model, contents, config):
        self.chamadas += 1
        return self._respostas.pop(0)


class _ClientFake:
    def __init__(self, respostas):
        self.models = _ModelsFake(respostas)


def teste_loop_function_calling_completa_apos_executar_ferramenta():
    GeminiSalesAgent._is_initialized = True
    GeminiSalesAgent._system_instruction_fc = "system fc de teste"

    resposta_1 = _RespostaFake(function_calls=[_FuncCallFake("adicionar_ao_carrinho", {"product_gid": "01PROD1", "delta_quantidade": 2})])
    resposta_2 = _RespostaFake(texto="Prontinho! Já ajustei o seu pedido para 2 pizzas.")
    fake_client = _ClientFake([resposta_1, resposta_2])
    GeminiSalesAgent._model = fake_client

    chamadas_executor = []
    def executor(nome, args):
        chamadas_executor.append((nome, args))
        return {"ok": True, "produto": "Pizza", "quantidade_final": 2}

    contexto = {"products": [{"id": 1, "gid": "01PROD1", "name": "Pizza", "price": 10.0}], "cart": []}
    resultado = GeminiSalesAgent.generate_response_with_tools("quero 2 pizzas", contexto, executor)

    assert fake_client.models.chamadas == 2, "esperava 2 chamadas: 1ª pede a função, 2ª devolve o texto final"
    assert len(chamadas_executor) == 1
    assert chamadas_executor[0] == ("adicionar_ao_carrinho", {"product_gid": "01PROD1", "delta_quantidade": 2})
    assert "Prontinho" in resultado["text"]
    assert resultado["acoes_executadas"][0]["resultado"]["ok"] is True
    print("OK  - loop function_call -> executor -> function_response -> texto final funciona")


def teste_loop_nao_afirma_sucesso_quando_executor_recusa():
    """O ponto central do F3: se o executor recusa, o texto final não pode dizer que
    adicionou — simulamos o modelo respeitando o resultado (é o que a instrução pede)."""
    GeminiSalesAgent._is_initialized = True
    GeminiSalesAgent._system_instruction_fc = "system fc de teste"

    resposta_1 = _RespostaFake(function_calls=[_FuncCallFake("adicionar_ao_carrinho", {"product_gid": "GID_INEXISTENTE", "delta_quantidade": 1})])
    resposta_2 = _RespostaFake(texto="Hmm, não encontrei esse produto no cardápio. Pode confirmar o nome?")
    fake_client = _ClientFake([resposta_1, resposta_2])
    GeminiSalesAgent._model = fake_client

    def executor(nome, args):
        return {"ok": False, "erro": "GID_FORA_DO_CATALOGO"}

    contexto = {"products": [], "cart": []}
    resultado = GeminiSalesAgent.generate_response_with_tools("quero um X", contexto, executor)

    acao = resultado["acoes_executadas"][0]
    assert acao["resultado"]["ok"] is False
    assert "não encontrei" in resultado["text"].lower() or "confirmar" in resultado["text"].lower()
    print("OK  - resultado de erro do executor fica registrado em acoes_executadas para telemetria")


# ── 4. Flag liga/desliga sem regressão no caminho padrão ───────────────────

def teste_flag_desligada_mantem_caminho_de_tags():
    assert HybridAIService._function_calling_habilitado() is False
    os.environ["IA_FUNCTION_CALLING"] = "true"
    assert HybridAIService._function_calling_habilitado() is True
    os.environ["IA_FUNCTION_CALLING"] = "false"
    assert HybridAIService._function_calling_habilitado() is False
    del os.environ["IA_FUNCTION_CALLING"]
    assert HybridAIService._function_calling_habilitado() is False
    print("OK  - flag IA_FUNCTION_CALLING lida corretamente (default: desligada)")


# ── 5. Integração completa: process_sales_chat com a flag ligada ───────────

def teste_process_sales_chat_com_flag_ligada_usa_novo_caminho():
    """Roda o pipeline síncrono inteiro (sessão -> pool -> Gemini -> carrinho) com
    IA_FUNCTION_CALLING=true, confirmando que HybridAIService de fato desvia para
    generate_response_with_tools em vez de generate_response (tags)."""
    from tests.test_pipeline_integration import (
        _montar_db_sqlite, _semear_restaurante_com_produtos, _indexar_fake,
    )

    db = _montar_db_sqlite()
    _, p1, _ = _semear_restaurante_com_produtos(db)
    _indexar_fake(db)

    GeminiSalesAgent._is_initialized = True

    chamou_generate_response = {"sim": False}
    chamou_generate_response_with_tools = {"sim": False}

    def _fake_generate_response(user_message, context):
        chamou_generate_response["sim"] = True
        return "não deveria ser chamado quando a flag está ligada"

    def _fake_generate_response_with_tools(user_message, context, executor):
        chamou_generate_response_with_tools["sim"] = True
        resultado_exec = executor("adicionar_ao_carrinho", {"product_gid": p1.gid, "delta_quantidade": 3})
        return {
            "text": "Prontinho, 3 pizzas adicionadas!",
            "acoes_executadas": [{"ferramenta": "adicionar_ao_carrinho",
                                   "args": {"product_gid": p1.gid, "delta_quantidade": 3},
                                   "resultado": resultado_exec}],
        }

    original_gr = GeminiSalesAgent.generate_response
    original_grwt = GeminiSalesAgent.generate_response_with_tools
    GeminiSalesAgent.generate_response = staticmethod(_fake_generate_response)
    GeminiSalesAgent.generate_response_with_tools = staticmethod(_fake_generate_response_with_tools)

    os.environ["IA_FUNCTION_CALLING"] = "true"
    try:
        resultado = HybridAIService.process_sales_chat(
            user_message="quero 3 pizzas margherita",
            restaurant_gid="01REST0000000000000000001",
            cart=[],
            db=db,
            session_id="sessao-fc-1",
        )
    finally:
        os.environ["IA_FUNCTION_CALLING"] = "false"
        GeminiSalesAgent.generate_response = original_gr
        GeminiSalesAgent.generate_response_with_tools = original_grwt

    assert chamou_generate_response_with_tools["sim"] is True, "deveria ter usado o caminho de function calling"
    assert chamou_generate_response["sim"] is False, "não deveria ter usado o caminho de tags com a flag ligada"
    assert resultado["cart"]["total_items"] == 3
    assert resultado["show_cart"] is True
    print("OK  - process_sales_chat com IA_FUNCTION_CALLING=true usa generate_response_with_tools e aplica a ação real")


if __name__ == "__main__":
    teste_ferramentas_tem_schema_esperado()
    teste_executor_rejeita_gid_fora_do_pool()
    teste_executor_aplica_teto_de_quantidade()
    teste_executor_delta_e_incremental_nao_absoluto()
    teste_executor_rejeita_produto_indisponivel()
    teste_executor_mostrar_sacola_seta_estado()
    teste_executor_sugerir_produtos_filtra_gids_fora_do_pool()
    teste_loop_function_calling_completa_apos_executar_ferramenta()
    teste_loop_nao_afirma_sucesso_quando_executor_recusa()
    teste_flag_desligada_mantem_caminho_de_tags()
    teste_process_sales_chat_com_flag_ligada_usa_novo_caminho()
    print("\nTodos os testes de function calling passaram (contra dublê — round-trip real ainda pendente).")
