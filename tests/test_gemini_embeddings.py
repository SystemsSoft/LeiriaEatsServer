"""
Testes do motor de embeddings do Gemini (services/gemini_embeddings.py) — sem rede: o
cliente do SDK é substituído por um dublê que regista os pedidos.

Execução:
    python3 tests/test_gemini_embeddings.py
"""
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

import torch

from services.e5_remoto import E5Indisponivel
from services.gemini_embeddings import GeminiEmbeddings


class _ClienteFalso:
    def __init__(self, falhar=False):
        self.pedidos = []
        self.falhar = falhar
        self.models = self

    def embed_content(self, model, contents, config):
        self.pedidos.append((config.task_type, list(contents)))
        if self.falhar:
            raise RuntimeError("quota")
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[float(len(c)), 1.0, 2.0]) for c in contents])


def _motor(pasta="", falhar=False):
    g = GeminiEmbeddings("chave-de-teste", pasta)
    g.cliente = _ClienteFalso(falhar)
    return g


def teste_prefixos_viram_tipos_de_tarefa():
    g = _motor()
    um = g.encode("query: pizza", convert_to_tensor=True)
    varios = g.encode(["passage: Pizza Margherita", "query: sushi"], convert_to_tensor=True)
    assert um.shape == (3,) and varios.shape == (2, 3), (um.shape, varios.shape)
    assert ("RETRIEVAL_QUERY", ["pizza"]) in g.cliente.pedidos
    assert ("RETRIEVAL_DOCUMENT", ["Pizza Margherita"]) in g.cliente.pedidos
    assert varios[0, 0].item() == len("Pizza Margherita") and varios[1, 0].item() == len("sushi")
    print("OK  - 'query:'/'passage:' viram RETRIEVAL_QUERY/RETRIEVAL_DOCUMENT, sem o prefixo")


def teste_produtos_ficam_em_cache_entre_arranques():
    pasta = tempfile.mkdtemp()
    g = _motor(pasta)
    g.encode(["passage: A", "passage: B"])
    g2 = _motor(pasta)  # novo arranque da API
    g2.encode(["passage: A", "passage: B", "passage: C"])
    assert g2.cliente.pedidos == [("RETRIEVAL_DOCUMENT", ["C"])], g2.cliente.pedidos
    g2.encode("query: x")
    g2.encode("query: x")
    assert len(g2.cliente.pedidos) == 3, "as pesquisas não ficam em cache"
    print("OK  - vetores dos produtos ficam em cache; num novo arranque só os novos vão à API")


def teste_falha_da_api_vira_e5_indisponivel():
    try:
        _motor(falhar=True).encode("query: pizza")
        raise AssertionError("devia levantar E5Indisponivel")
    except E5Indisponivel:
        pass
    print("OK  - erro da API do Gemini levanta E5Indisponivel (a pesquisa cai para o plano B léxico)")


def teste_limiares_do_e5_convertidos_para_a_escala_do_gemini():
    # A média das pontuações do E5 (0,778) corresponde à média do Gemini (0,615).
    assert abs(GeminiEmbeddings.ajustar_limiar(0.778) - 0.615) < 1e-9
    assert GeminiEmbeddings.ajustar_limiar(0.60) < 0.60
    assert GeminiEmbeddings.ajustar_limiar(0.45) < GeminiEmbeddings.ajustar_limiar(0.60) < GeminiEmbeddings.ajustar_limiar(0.65)
    print("OK  - limiares fixos do E5 convertidos para a escala do Gemini, mantendo a ordem")


if __name__ == "__main__":
    teste_prefixos_viram_tipos_de_tarefa()
    teste_produtos_ficam_em_cache_entre_arranques()
    teste_falha_da_api_vira_e5_indisponivel()
    teste_limiares_do_e5_convertidos_para_a_escala_do_gemini()
    print("\nTodos os testes do motor de embeddings do Gemini passaram.")
