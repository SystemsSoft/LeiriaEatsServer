"""
Testes do failover de chaves/modelos do chat por texto em streaming
(GeminiSalesAgent.generate_response_stream) — sem rede e sem gastar cota: os clientes Gemini são dublês.

Cobrem o que causou "o chat sempre devolve a mesma resposta estática" (24/09/2026): com o tier gratuito
congestionado, cada chave gratuita gastava até 10s (timeout da requisição) e o orçamento de 20s do turno
acabava antes de a chave PAGA — a única saudável — ser tentada.

Execução:
    python3 tests/test_gemini_stream_failover.py
"""
import os
import sys
import time
import uuid
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

from services.gemini_sales_service import GeminiSalesAgent as G

PRODUTOS = [{"id": 1, "gid": "G1", "name": "Burrito Vegetariano", "price": 10.0, "description": "",
             "category": "Burrito", "restaurant_name": "Mexicana", "restaurant_gid": "R1", "is_available": True}]


class _ClienteFake:
    """Cliente Gemini falso. `comportamento`: "ok" | "lento" (não entrega o 1º trecho a tempo) |
    "503" | "403". Registra as chamadas para conferir a ordem das chaves e dos modelos."""

    def __init__(self, rotulo, comportamento, chamadas):
        self.rotulo = rotulo
        self.comportamento = comportamento
        self.chamadas = chamadas
        self.models = SimpleNamespace(generate_content_stream=self._stream)

    def _stream(self, model, contents, config):
        self.chamadas.append((self.rotulo, model))
        if self.comportamento == "503":
            raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")
        if self.comportamento == "403":
            raise RuntimeError("403 PERMISSION_DENIED. Your project has been denied access.")
        if self.comportamento == "lento":
            time.sleep(1.5)  # bem acima do limite de 1º trecho usado nos testes
        return iter([SimpleNamespace(text="Olá, "), SimpleNamespace(text="tudo bem?")])


def _rodar(comportamentos, modelos_falham=(), limite_ttft=0.3):
    """Roda UM turno com clientes falsos (`comportamentos` na ordem do .env: a ÚLTIMA é a paga).
    Devolve (texto, chamadas, segundos)."""
    chamadas = []
    original = (G._clients, G._is_initialized, G._TTFT_LIMITE_S, dict(G._chaves_com_falha), dict(G._modelos_com_falha))
    G._clients = [_ClienteFake(f"k{i}", c, chamadas) for i, c in enumerate(comportamentos)]
    G._is_initialized = True
    G._system_instruction = "INSTRUCAO-FAKE"
    G._TTFT_LIMITE_S = limite_ttft
    G._chaves_com_falha = {}
    G._modelos_com_falha = {}
    try:
        ctx = {"products": PRODUTOS, "cart": [], "history_text": "", "session_context": {}, "intent_type": "product_search"}
        t = time.time()
        texto = "".join(G.generate_response_stream(f"quero um burrito {uuid.uuid4().hex}", ctx))
        return texto, chamadas, time.time() - t
    finally:
        G._clients, G._is_initialized, G._TTFT_LIMITE_S, G._chaves_com_falha, G._modelos_com_falha = original


def teste_ordem_das_chaves_paga_em_terceiro():
    assert G.ordem_base_das_chaves(5) == [0, 1, 4, 2, 3]
    assert G.ordem_base_das_chaves(3) == [0, 1, 2]
    assert G.ordem_base_das_chaves(2) == [0, 1]
    print("OK  - ordem das chaves: 1ª e 2ª gratuitas, paga em 3º, demais gratuitas no fim")


def teste_gratuita_saudavel_responde_sem_tocar_nas_outras():
    texto, chamadas, dt = _rodar(["ok", "ok", "ok", "ok", "ok"])
    assert texto == "Olá, tudo bem?"
    assert [c[0] for c in chamadas] == ["k0"], chamadas
    print("OK  - gratuita saudável responde e as demais chaves nem são chamadas")


def teste_gratuita_lenta_e_abandonada_no_limite_e_a_paga_responde():
    # k0 e k1 (gratuitas) travam no 1º trecho; k4 é a paga
    texto, chamadas, dt = _rodar(["lento", "lento", "ok", "ok", "ok"])
    assert texto == "Olá, tudo bem?", texto
    assert [c[0] for c in chamadas] == ["k0", "k1", "k4"], f"ordem inesperada: {chamadas}"
    assert dt < 1.2, f"esperou {dt:.2f}s: deveria abandonar cada gratuita em ~0,3s, não esperar os 1,5s delas"
    # e o modelo é o MESMO nas 3 tentativas — trocar de modelo antes de tentar a paga era o defeito
    assert len({c[1] for c in chamadas}) == 1, chamadas
    print(f"OK  - 2 gratuitas lentas abandonadas no limite; a paga (3ª) responde em {dt:.2f}s, sem trocar de modelo")


def teste_erro_503_nas_gratuitas_cai_na_paga():
    texto, chamadas, dt = _rodar(["503", "503", "ok", "ok", "ok"])
    assert texto == "Olá, tudo bem?"
    assert [c[0] for c in chamadas] == ["k0", "k1", "k4"], chamadas
    print("OK  - 503 nas duas gratuitas cai na paga")


def teste_so_troca_de_modelo_quando_a_paga_tambem_falha():
    # todas as chaves do 1º modelo dão 503, mas o 2º modelo (reserva) responde? O dublê não distingue modelo —
    # então aqui basta conferir que, com a paga falhando, o próximo modelo É tentado (começa de novo pela k0).
    chamadas = []
    original = (G._clients, G._is_initialized, G._TTFT_LIMITE_S, dict(G._chaves_com_falha), dict(G._modelos_com_falha))

    class _PorModelo(_ClienteFake):
        def _stream(self, model, contents, config):
            self.comportamento = "503" if model == G.MODELO_ESTAVEL else "ok"
            return super()._stream(model, contents, config)

    G._clients = [_PorModelo(f"k{i}", "ok", chamadas) for i in range(5)]
    G._is_initialized = True
    G._system_instruction = "INSTRUCAO-FAKE"
    G._TTFT_LIMITE_S = 0.3
    G._chaves_com_falha, G._modelos_com_falha = {}, {}
    try:
        ctx = {"products": PRODUTOS, "cart": [], "history_text": "", "session_context": {}, "intent_type": "product_search"}
        texto = "".join(G.generate_response_stream(f"quero um burrito {uuid.uuid4().hex}", ctx))
    finally:
        G._clients, G._is_initialized, G._TTFT_LIMITE_S, G._chaves_com_falha, G._modelos_com_falha = original
    assert texto == "Olá, tudo bem?", texto
    modelos = [c[1] for c in chamadas]
    assert modelos[:3] == [G.MODELO_ESTAVEL] * 3, f"as 3 primeiras tentativas deveriam ser do modelo principal: {chamadas}"
    assert [c[0] for c in chamadas[:3]] == ["k0", "k1", "k4"], chamadas
    assert modelos[3] == G.MODELO_RESERVA, f"depois da paga falhar deveria ir ao modelo de reserva: {chamadas}"
    print("OK  - troca para o modelo de reserva só depois de a paga também falhar")


def teste_chave_ruim_e_lembrada_no_turno_seguinte():
    chamadas = []
    original = (G._clients, G._is_initialized, G._TTFT_LIMITE_S, dict(G._chaves_com_falha), dict(G._modelos_com_falha))
    G._clients = [_ClienteFake("k0", "lento", chamadas), _ClienteFake("k1", "lento", chamadas),
                  _ClienteFake("k2", "ok", chamadas), _ClienteFake("k3", "ok", chamadas), _ClienteFake("k4", "ok", chamadas)]
    G._is_initialized = True
    G._system_instruction = "INSTRUCAO-FAKE"
    G._TTFT_LIMITE_S = 0.3
    G._chaves_com_falha, G._modelos_com_falha = {}, {}
    try:
        ctx = {"products": PRODUTOS, "cart": [], "history_text": "", "session_context": {}, "intent_type": "product_search"}
        "".join(G.generate_response_stream(f"turno 1 {uuid.uuid4().hex}", ctx))
        chamadas.clear()
        t = time.time()
        "".join(G.generate_response_stream(f"turno 2 {uuid.uuid4().hex}", ctx))
        dt = time.time() - t
    finally:
        G._clients, G._is_initialized, G._TTFT_LIMITE_S, G._chaves_com_falha, G._modelos_com_falha = original
    assert [c[0] for c in chamadas] == ["k4"], f"no 2º turno a paga deveria ir direto: {chamadas}"
    assert dt < 0.3, f"2º turno levou {dt:.2f}s"
    print("OK  - no turno seguinte as gratuitas lentas não são tentadas de novo: a paga vai direto")


def teste_todas_falhando_devolve_o_texto_de_emergencia_honesto():
    texto, chamadas, dt = _rodar(["503", "503", "503", "503", "503"])
    assert "ainda não o fiz" in texto and "Burrito Vegetariano" in texto, texto
    print("OK  - tudo falhando: texto de emergência avisa que o pedido NÃO foi processado")


if __name__ == "__main__":
    teste_ordem_das_chaves_paga_em_terceiro()
    teste_gratuita_saudavel_responde_sem_tocar_nas_outras()
    teste_gratuita_lenta_e_abandonada_no_limite_e_a_paga_responde()
    teste_erro_503_nas_gratuitas_cai_na_paga()
    teste_so_troca_de_modelo_quando_a_paga_tambem_falha()
    teste_chave_ruim_e_lembrada_no_turno_seguinte()
    teste_todas_falhando_devolve_o_texto_de_emergencia_honesto()
    print("\nTodos os testes do failover do chat em streaming passaram (clientes Gemini simulados).")
