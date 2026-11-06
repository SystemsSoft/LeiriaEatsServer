"""
Testes do índice da pesquisa com vários workers do uvicorn (services/ai_service.py):
quem altera o índice incrementa uma versão no Redis e os outros workers recarregam.
Sem rede: o Redis é um dublê em memória.

Execução:
    python3 tests/test_varios_workers.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

from services.ai_service import AIService
from services.session_service import SessionManager


class _RedisFalso:
    def __init__(self):
        self.dados = {}

    def incr(self, chave):
        self.dados[chave] = int(self.dados.get(chave, 0)) + 1
        return self.dados[chave]

    def get(self, chave):
        valor = self.dados.get(chave)
        return None if valor is None else str(valor).encode()


def _com_redis(redis_falso, corpo):
    original_redis = SessionManager._get_redis
    original_recarregar = AIService._recarregar_por_outro_worker
    original_versao = AIService._versao_indice
    recargas = []
    SessionManager._get_redis = classmethod(lambda cls: redis_falso)
    AIService._recarregar_por_outro_worker = classmethod(lambda cls: recargas.append(cls._versao_indice))
    try:
        corpo(recargas)
    finally:
        SessionManager._get_redis = original_redis
        AIService._recarregar_por_outro_worker = original_recarregar
        AIService._versao_indice = original_versao


def teste_primeira_pesquisa_so_regista_a_versao():
    redis_falso = _RedisFalso()
    redis_falso.incr(AIService._CHAVE_VERSAO_INDICE)

    def corpo(recargas):
        AIService._versao_indice = None
        AIService._sincronizar_com_outros_workers()
        assert AIService._versao_indice == 1 and recargas == [], (AIService._versao_indice, recargas)

    _com_redis(redis_falso, corpo)
    print("OK  - 1ª pesquisa de um worker regista a versão sem recarregar (o índice do arranque está atual)")


def teste_alteracao_noutro_worker_dispara_uma_recarga():
    redis_falso = _RedisFalso()

    def corpo(recargas):
        AIService._versao_indice = None
        AIService._sincronizar_com_outros_workers()      # worker A regista a versão 0
        redis_falso.incr(AIService._CHAVE_VERSAO_INDICE)  # worker B editou um produto
        AIService._sincronizar_com_outros_workers()
        AIService._sincronizar_com_outros_workers()      # a mesma versão não recarrega de novo
        import threading
        for t in threading.enumerate():
            if t.name == "recarregar-indice":
                t.join(timeout=2)
        assert recargas == [1], recargas

    _com_redis(redis_falso, corpo)
    print("OK  - alteração feita noutro worker dispara exatamente uma recarga do índice")


def teste_quem_altera_publica_e_nao_se_recarrega_a_si_proprio():
    redis_falso = _RedisFalso()

    def corpo(recargas):
        AIService._versao_indice = None
        AIService._sincronizar_com_outros_workers()
        AIService._publicar_alteracao_indice()           # este worker editou um produto
        AIService._sincronizar_com_outros_workers()
        assert redis_falso.dados[AIService._CHAVE_VERSAO_INDICE] == 1
        assert recargas == [], recargas

    _com_redis(redis_falso, corpo)
    print("OK  - o worker que altera o índice publica a versão e não recarrega o que já tem")


def teste_sem_redis_nada_muda():
    original = SessionManager._get_redis
    SessionManager._get_redis = classmethod(lambda cls: None)
    try:
        AIService._publicar_alteracao_indice()
        AIService._sincronizar_com_outros_workers()
    finally:
        SessionManager._get_redis = original
    print("OK  - sem Redis (1 worker) publicar/sincronizar não faz nada")


if __name__ == "__main__":
    teste_primeira_pesquisa_so_regista_a_versao()
    teste_alteracao_noutro_worker_dispara_uma_recarga()
    teste_quem_altera_publica_e_nao_se_recarrega_a_si_proprio()
    teste_sem_redis_nada_muda()
    print("\nTodos os testes de vários workers passaram.")
