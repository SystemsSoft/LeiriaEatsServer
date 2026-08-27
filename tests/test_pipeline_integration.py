"""
Teste de integração de ponta a ponta do pipeline de chat (F0-F2 do PLANO_EXECUCAO_IA.md).

Roda process_sales_chat_stream contra um SQLite em memória (não a base MySQL real de
core/database.py) e com o Gemini substituído por um dublê. Cobre a cadeia inteira:
sessão → pool de produtos (com filtro de GID) → geração → parsing de tags → carrinho →
telemetria — para garantir que os ajustes de F1/F2, feitos em vários pontos do mesmo
arquivo, continuam funcionando juntos.

Execução:
    python3 tests/test_pipeline_integration.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")  # força SessionManager a usar fallback em memória

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base
from core.sql_models import RestaurantDB, ProductDB
from services.ai_service import AIService
from services.gemini_sales_service import GeminiSalesAgent
from services.hybrid_ai_service import HybridAIService
from services.session_service import SessionManager


def _montar_db_sqlite():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _semear_restaurante_com_produtos(db):
    r = RestaurantDB(
        name="Pizzaria Teste", category="Pizzaria", login="teste", password="x",
        gid="01REST0000000000000000001",
    )
    db.add(r)
    db.commit()
    db.refresh(r)

    p1 = ProductDB(
        gid="01PROD0000000000000000001", name="Pizza Margherita", description="Molho e queijo",
        price=12.5, category="Pizza", restaurant_id=r.id, is_available=True,
    )
    # Produto de propósito SEM gid — deve ser excluído do pool (F1.4)
    p2 = ProductDB(
        gid=None, name="Produto Órfão Sem GID", description="não deveria aparecer no pool",
        price=5.0, category="Pizza", restaurant_id=r.id, is_available=True,
    )
    db.add_all([p1, p2])
    db.commit()
    return r, p1, p2


def _indexar_fake(db):
    """Substitui o carregamento do modelo E5 real por um dublê determinístico,
    e popula o cache/índice do AIService a partir do banco de teste."""
    import torch

    class _ModeloFake:
        def encode(self, textos, convert_to_tensor=True):
            return torch.zeros((len(textos), 4))

    AIService.get_model = classmethod(lambda cls: _ModeloFake())
    AIService.reload_data(db)


class _FakeModels:
    def __init__(self, texto_resposta):
        self._texto = texto_resposta

    def generate_content_stream(self, model, contents, config):
        # Simula o SDK do Gemini devolvendo um único "chunk" com todo o texto.
        class _Chunk:
            def __init__(self, text):
                self.text = text
        yield _Chunk(self._texto)


class _FakeClient:
    def __init__(self, texto_resposta):
        self.models = _FakeModels(texto_resposta)


def teste_pipeline_completo_adiciona_ao_carrinho():
    db = _montar_db_sqlite()
    _, p1, p2 = _semear_restaurante_com_produtos(db)
    _indexar_fake(db)

    # Confirma F1.4 antes de rodar o pipeline: produto sem GID não deve estar indexável
    assert AIService._product_by_id.get(p1.id) is not None
    assert AIService._product_by_id.get(p2.id) is not None  # ainda está no cache bruto...

    GeminiSalesAgent._is_initialized = True
    GeminiSalesAgent._system_instruction = "system de teste"
    GeminiSalesAgent._model = _FakeClient(
        f"Perfeito! Já vou adicionar. [[ADD_TO_CART:{p1.gid}:2]]"
    )

    eventos = list(HybridAIService.process_sales_chat_stream(
        user_message="quero 2 pizzas margherita",
        restaurant_gid="01REST0000000000000000001",
        db=db,
        session_id="sessao-teste-1",
    ))

    finais = [e for e in eventos if e.get("type") == "final"]
    assert len(finais) == 1, f"esperava exatamente 1 evento final, houve {len(finais)}"
    final = finais[0]

    assert final["cart"]["total_items"] == 2, f"carrinho deveria ter 2 itens: {final['cart']}"
    assert final["show_cart"] is True

    # Produto sem GID (p2) não pode aparecer nos produtos oferecidos à IA
    gids_oferecidos = {p["gid"] for p in final.get("cartProducts", []) + final.get("products", [])}
    assert "" not in gids_oferecidos and None not in gids_oferecidos

    print("OK  - pipeline completo (sessão → pool → Gemini → tag → carrinho) funciona de ponta a ponta")
    print("OK  - produto sem GID não vaza para o payload de resposta")


def teste_gid_fora_do_pool_nao_quebra_pipeline():
    """Modelo tenta adicionar um GID que não existe no pool — não deve derrubar o turno,
    apenas descartar a tag (comportamento documentado em F0/F1.4)."""
    db = _montar_db_sqlite()
    _semear_restaurante_com_produtos(db)
    _indexar_fake(db)

    GeminiSalesAgent._is_initialized = True
    GeminiSalesAgent._system_instruction = "system de teste"
    GeminiSalesAgent._model = _FakeClient("Vou adicionar! [[ADD_TO_CART:GIDQUENAOEXISTE:1]]")

    eventos = list(HybridAIService.process_sales_chat_stream(
        user_message="quero uma pizza",
        restaurant_gid="01REST0000000000000000001",
        db=db,
        session_id="sessao-teste-2",
    ))

    erros = [e for e in eventos if e.get("type") == "error"]
    assert not erros, f"pipeline não deveria levantar erro: {erros}"

    finais = [e for e in eventos if e.get("type") == "final"]
    assert len(finais) == 1
    assert finais[0]["cart"]["total_items"] == 0, "GID inexistente não deveria alterar o carrinho"

    print("OK  - GID fora do pool é descartado sem derrubar o pipeline")


if __name__ == "__main__":
    teste_pipeline_completo_adiciona_ao_carrinho()
    teste_gid_fora_do_pool_nao_quebra_pipeline()
    print("\nTodos os testes de integração do pipeline passaram.")
