"""
Testes do cliente do serviço de embeddings (services/e5_remoto.py) e do plano B da
pesquisa quando o serviço não responde — sem rede nem modelo: o serviço é um
servidor HTTP falso local.

Execução:
    python3 tests/test_e5_remoto.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

import torch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base
from core.sql_models import RestaurantDB, ProductDB
from services.ai_service import AIService
from services.e5_remoto import E5Indisponivel, E5Remoto

DIM = 8
pedidos_recebidos = []


def _vetor(texto):
    return [float((hash(texto) >> (4 * i)) % 7) for i in range(DIM)]


class _ServicoFalso(BaseHTTPRequestHandler):
    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        pedidos_recebidos.append({"token": self.headers.get("X-E5-Token"), "n": len(corpo["textos"])})
        if self.headers.get("X-E5-Token") != "segredo":
            self.send_response(401)
            self.end_headers()
            return
        resposta = json.dumps({"vetores": [_vetor(t) for t in corpo["textos"]]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resposta)))
        self.end_headers()
        self.wfile.write(resposta)

    def do_GET(self):
        resposta = json.dumps({"ok": True, "dim": DIM, "modelo": "m1"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resposta)))
        self.end_headers()
        self.wfile.write(resposta)

    def log_message(self, *args):
        pass


def _servidor():
    srv = HTTPServer(("127.0.0.1", 0), _ServicoFalso)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _url_fechada():
    srv = HTTPServer(("127.0.0.1", 0), _ServicoFalso)
    porta = srv.server_address[1]
    srv.server_close()
    return f"http://127.0.0.1:{porta}"


def teste_encode_imita_o_sentence_transformer():
    srv, url = _servidor()
    try:
        e5 = E5Remoto(url, token="segredo")
        um = e5.encode("query: pizza", convert_to_tensor=True)
        varios = e5.encode(["passage: a", "passage: b", "passage: c"], convert_to_tensor=True)
        assert isinstance(um, torch.Tensor) and um.shape == (DIM,), um.shape
        assert varios.shape == (3, DIM) and varios.dtype == torch.float32, varios.shape
        assert um.tolist() == _vetor("query: pizza")
        assert pedidos_recebidos[-1]["token"] == "segredo"
    finally:
        srv.shutdown()
    print("OK  - encode devolve tensores com a mesma forma do SentenceTransformer e envia o token")


def teste_catalogo_grande_vai_em_lotes():
    srv, url = _servidor()
    try:
        pedidos_recebidos.clear()
        vetores = E5Remoto(url, token="segredo").encode([f"passage: {i}" for i in range(300)], convert_to_tensor=True)
        assert vetores.shape == (300, DIM)
        assert [p["n"] for p in pedidos_recebidos] == [4] * 75, pedidos_recebidos
    finally:
        srv.shutdown()
    print("OK  - 300 textos são enviados em lotes de 4 (cada pedido fica bem abaixo do timeout)")


def teste_vetores_dos_produtos_ficam_em_cache_no_disco():
    srv, url = _servidor()
    pasta = tempfile.mkdtemp()
    try:
        produtos = ["passage: Pizza Margherita", "passage: Sushi Salmão", "passage: Taco"]
        pedidos_recebidos.clear()
        primeiro = E5Remoto(url, token="segredo", pasta_cache=pasta).encode(produtos, convert_to_tensor=True)
        assert sum(p["n"] for p in pedidos_recebidos) == 3

        # Novo arranque da API (novo cliente): só o produto novo vai ao serviço.
        pedidos_recebidos.clear()
        e5 = E5Remoto(url, token="segredo", pasta_cache=pasta)
        segundo = e5.encode(produtos + ["passage: Burrito"], convert_to_tensor=True)
        assert [p["n"] for p in pedidos_recebidos] == [1], pedidos_recebidos
        assert torch.equal(segundo[:3], primeiro) and segundo[3].tolist() == _vetor("passage: Burrito")

        # As pesquisas dos utilizadores ("query: ...") não ficam em cache.
        pedidos_recebidos.clear()
        e5.encode("query: pizza")
        e5.encode("query: pizza")
        assert len(pedidos_recebidos) == 2, pedidos_recebidos
    finally:
        srv.shutdown()
    print("OK  - vetores dos produtos ficam em cache no disco; num novo arranque só os novos vão ao serviço")


def teste_servico_em_baixo_ou_token_errado_levanta_e5_indisponivel():
    for e5 in (E5Remoto(_url_fechada(), token="segredo", timeout=2),):
        try:
            e5.encode("query: x")
            raise AssertionError("devia ter levantado E5Indisponivel")
        except E5Indisponivel:
            pass
    srv, url = _servidor()
    try:
        try:
            E5Remoto(url, token="errado").encode("query: x")
            raise AssertionError("token errado devia levantar E5Indisponivel")
        except E5Indisponivel:
            pass
    finally:
        srv.shutdown()
    print("OK  - serviço em baixo ou token errado levantam E5Indisponivel")


def teste_pesquisa_cai_para_lexica_sem_o_servico():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    r = RestaurantDB(name="Rest Teste", category="Pizzaria", login="t", password="x", gid="01REST1")
    db.add(r)
    db.commit()
    db.refresh(r)
    db.add_all([
        ProductDB(gid="01P1", name="Pizza Margherita", description="", search_tags="", price=9.0,
                  category="Pizza", restaurant_id=r.id, is_available=True),
        ProductDB(gid="01P2", name="Sushi Salmão", description="", search_tags="", price=12.0,
                  category="Sushi", restaurant_id=r.id, is_available=True),
    ])
    db.commit()

    estado = (AIService._model, AIService._data_cache, AIService._embeddings_products,
              AIService._ESPERA_REINDEXACAO)
    AIService._model = E5Remoto(_url_fechada(), timeout=2)
    AIService._data_cache = None
    AIService._embeddings_products = None
    AIService._ESPERA_REINDEXACAO = 0.05
    srv = None
    try:
        t0 = time.monotonic()
        resposta = AIService.process_search("margherita", db)
        assert resposta.intent == "product_search", resposta.intent
        assert [p.name for p in resposta.productResults] == ["Pizza Margherita"], resposta.productResults
        vazia = AIService.process_search("algo totalmente diferente", db)
        assert vazia.intent == "no_match" and not vazia.productResults
        for _ in range(5):
            AIService.process_search("sushi", db)
        # Nenhuma pesquisa espera pela reindexação (antes, uma delas ficava ~2 min sem resposta).
        assert time.monotonic() - t0 < 5, time.monotonic() - t0
        reindexadoras = [t for t in threading.enumerate() if t.name == "reindexar-e5"]
        assert len(reindexadoras) == 1, reindexadoras

        # O serviço volta: a thread reconstrói o índice sozinha e a pesquisa volta a ser semântica.
        srv, url = _servidor()
        AIService._model = E5Remoto(url, token="segredo")
        reindexadoras[0].join(timeout=5)
        assert not reindexadoras[0].is_alive() and AIService._embeddings_products is not None
        assert AIService.process_search("sushi", db).productResults
    finally:
        if srv:
            srv.shutdown()
        (AIService._model, AIService._data_cache, AIService._embeddings_products,
         AIService._ESPERA_REINDEXACAO) = estado
    print("OK  - sem o serviço de E5 a pesquisa responde já (léxica) e o índice volta sozinho em segundo plano")


if __name__ == "__main__":
    teste_encode_imita_o_sentence_transformer()
    teste_catalogo_grande_vai_em_lotes()
    teste_vetores_dos_produtos_ficam_em_cache_no_disco()
    teste_servico_em_baixo_ou_token_errado_levanta_e5_indisponivel()
    teste_pesquisa_cai_para_lexica_sem_o_servico()
    print("\nTodos os testes do cliente E5 remoto passaram.")
