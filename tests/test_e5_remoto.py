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
import threading
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
        assert [p["n"] for p in pedidos_recebidos] == [8] * 37 + [4], pedidos_recebidos
    finally:
        srv.shutdown()
    print("OK  - 300 textos são enviados em lotes de 8 (cada pedido fica bem abaixo do timeout)")


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
              AIService._ultima_tentativa_indice, AIService._index_data)
    AIService._model = E5Remoto(_url_fechada(), timeout=2)
    AIService._data_cache = None
    AIService._embeddings_products = None
    tentativas = []
    indexar_original = AIService._index_data.__func__

    def _index_data_contado(cls, data):
        tentativas.append(1)
        return indexar_original(cls, data)

    AIService._index_data = classmethod(_index_data_contado)
    try:
        resposta = AIService.process_search("margherita", db)
        assert resposta.intent == "product_search", resposta.intent
        assert [p.name for p in resposta.productResults] == ["Pizza Margherita"], resposta.productResults
        vazia = AIService.process_search("algo totalmente diferente", db)
        assert vazia.intent == "no_match" and not vazia.productResults
        for _ in range(5):
            AIService.process_search("sushi", db)
        # 1ª pesquisa: reload_data tenta indexar; 2ª: o retry do process_search. Depois disso,
        # dentro do mesmo minuto, nenhuma pesquisa volta a tentar (era isso que enchia o serviço).
        assert len(tentativas) == 2, tentativas
    finally:
        (AIService._model, AIService._data_cache, AIService._embeddings_products,
         AIService._ultima_tentativa_indice, AIService._index_data) = estado
    print("OK  - sem o serviço de E5 a pesquisa devolve resultados léxicos e só tenta reindexar 1x por minuto")


if __name__ == "__main__":
    teste_encode_imita_o_sentence_transformer()
    teste_catalogo_grande_vai_em_lotes()
    teste_servico_em_baixo_ou_token_errado_levanta_e5_indisponivel()
    teste_pesquisa_cai_para_lexica_sem_o_servico()
    print("\nTodos os testes do cliente E5 remoto passaram.")
