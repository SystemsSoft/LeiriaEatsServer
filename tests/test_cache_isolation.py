"""
Teste de isolamento do cache do GeminiSalesAgent (F1.3 do PLANO_EXECUCAO_IA.md).

Não chama a API real — o cliente Gemini é substituído por um dublê (fake) que devolve
respostas controladas, para exercitar apenas a lógica de cache.

Cobre a regressão original: duas sessões com carrinhos diferentes emitindo a mesma
mensagem curta ("sim") não podem compartilhar resposta cacheada, e nenhuma resposta
contendo tag de ação ([[ADD_TO_CART:...]]) pode ser cacheada.

Execução (sem pytest instalado no projeto):
    python3 tests/test_cache_isolation.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Config precisa de uma GEMINI_API_KEY presente só para não emitir aviso; não é usada
# porque o cliente é substituído por um dublê antes de qualquer chamada de rede.
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")

from services.gemini_sales_service import GeminiSalesAgent, GeminiCache, GeminiUsageMonitor


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeModels:
    """Substitui cls._model.models — devolve respostas pré-definidas em sequência."""

    def __init__(self, respostas):
        self._respostas = list(respostas)
        self.chamadas = 0

    def generate_content(self, model, contents, config):
        self.chamadas += 1
        texto = self._respostas.pop(0) if self._respostas else "resposta padrão"
        return _FakeResponse(texto)


class _FakeClient:
    def __init__(self, respostas):
        self.models = _FakeModels(respostas)


def _preparar_agente(respostas):
    """Reseta o estado de classe do GeminiSalesAgent e injeta o dublê de cliente."""
    GeminiSalesAgent._is_initialized = True
    GeminiSalesAgent._cache = GeminiCache(ttl_seconds=1800)
    GeminiSalesAgent._usage_monitor = GeminiUsageMonitor()
    GeminiSalesAgent._system_instruction = "system instruction de teste"
    fake_client = _FakeClient(respostas)
    GeminiSalesAgent._clients = [fake_client]
    return fake_client


def _item_carrinho(product_id: int, quantity: int) -> dict:
    """Formato real de CartItem.to_dict() (services/session_service.py)."""
    return {
        "product_id": product_id,
        "name": "Pizza",
        "price": 10.0,
        "restaurant_gid": "01REST1",
        "quantity": quantity,
        "serves_people": 1,
        "category": "",
        "subtotal": round(10.0 * quantity, 2),
    }


def _contexto(cart_quantities, produtos=None, historico=""):
    """cart_quantities: lista de (product_id, quantity)."""
    return {
        "products": produtos or [{"id": 1, "gid": "01PROD1", "name": "Pizza", "price": 10.0}],
        "cart": [_item_carrinho(pid, qty) for pid, qty in cart_quantities],
        "history_text": historico,
        "session_context": {},
    }


def teste_nao_compartilha_resposta_com_tag_entre_sessoes():
    """
    Regressão original: sessão A (carrinho com 1x Pizza) pergunta "sim" e o modelo
    responde adicionando ao carrinho via tag. Sessão B, com carrinho DIFERENTE, pergunta
    "sim" também — não pode receber a mesma resposta cacheada (que executaria a ação de A).
    """
    resposta_com_tag_A = "Perfeito! Vou adicionar. [[ADD_TO_CART:01PROD1:2]]"
    resposta_para_B = "Combinado, já ajusto por aqui. [[ADD_TO_CART:01PROD1:1]]"

    fake = _preparar_agente([resposta_com_tag_A, resposta_para_B])

    cart_A = [(1, 1)]
    cart_B = [(1, 5)]

    resp_A = GeminiSalesAgent.generate_response("sim", _contexto(cart_A))
    resp_B = GeminiSalesAgent.generate_response("sim", _contexto(cart_B))

    assert fake.models.chamadas == 2, (
        f"esperava 2 chamadas reais à API (uma por sessão), houve {fake.models.chamadas} "
        "— indica que a resposta com tag foi servida do cache para a segunda sessão"
    )
    assert resp_A != resp_B, "respostas de sessões com carrinhos diferentes vieram iguais"

    entrada_cacheada_A = GeminiSalesAgent._cache.get(
        GeminiSalesAgent._generate_cache_key("sim", _contexto(cart_A))
    )
    assert entrada_cacheada_A is None, (
        "resposta com tag [[ADD_TO_CART...]] não deveria ter sido gravada no cache"
    )
    print("OK  - respostas com tag não são compartilhadas entre sessões com carrinhos distintos")


def teste_resposta_com_tag_nunca_e_cacheada():
    """Mesma sessão, duas chamadas idênticas: se a resposta tem tag, a segunda chamada
    deve ir à API de novo (cache puro faria só 1 chamada)."""
    fake = _preparar_agente([
        "Adicionando já! [[ADD_TO_CART:01PROD1:1]]",
        "Adicionando já! [[ADD_TO_CART:01PROD1:1]]",
    ])
    ctx = _contexto([(1, 0)])

    GeminiSalesAgent.generate_response("quero uma pizza", ctx)
    GeminiSalesAgent.generate_response("quero uma pizza", ctx)

    assert fake.models.chamadas == 2, (
        f"esperava 2 chamadas (resposta com tag nunca deve ser cacheada), houve {fake.models.chamadas}"
    )
    print("OK  - resposta contendo tag [[...]] nunca é gravada no cache")


def teste_resposta_sem_tag_ainda_usa_cache():
    """Garantia de que a correção não desligou o cache por completo: resposta sem
    ação de carrinho, na MESMA sessão/contexto, deve ser servida do cache na 2ª chamada."""
    fake = _preparar_agente([
        "Temos pizza e hambúrguer disponíveis, qual prefere?",
        "não deveria ser usada",
    ])
    ctx = _contexto([(1, 0)])

    r1 = GeminiSalesAgent.generate_response("o que vocês têm?", ctx)
    r2 = GeminiSalesAgent.generate_response("o que vocês têm?", ctx)

    assert fake.models.chamadas == 1, (
        f"esperava 1 chamada (2ª deveria vir do cache), houve {fake.models.chamadas}"
    )
    assert r1 == r2
    print("OK  - resposta sem tag continua sendo cacheada normalmente")


if __name__ == "__main__":
    teste_nao_compartilha_resposta_com_tag_entre_sessoes()
    teste_resposta_com_tag_nunca_e_cacheada()
    teste_resposta_sem_tag_ainda_usa_cache()
    print("\nTodos os testes de isolamento de cache passaram.")
