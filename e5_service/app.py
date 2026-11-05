"""Serviço de embeddings E5 (int8) do Koma.

Só calcula vetores: recebe textos já com o prefixo do E5 ("query: ..." ou
"passage: ...") e devolve os embeddings. O índice, os limiares e a lógica de
pesquisa ficam na API (services/ai_service.py), que chama este serviço através de
services/e5_remoto.py quando E5_SERVICE_URL está definido.

Variáveis de ambiente:
    E5_SERVICE_TOKEN  se definido, exige o mesmo valor no cabeçalho X-E5-Token
    E5_MODELO         caminho do modelo quantizado (padrão: /modelo/e5_int8.pt)
"""
import os
import threading
from typing import List, Optional

import torch
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

MAX_TEXTOS = 256
TOKEN = os.getenv("E5_SERVICE_TOKEN", "")

# O motor tem de estar definido antes de carregar: os pesos int8 foram empacotados para o qnnpack.
torch.backends.quantized.engine = "qnnpack"
# Dentro do container o torch arrancava com 1 thread; o cálculo tem os vCPU da instância.
torch.set_num_threads(int(os.getenv("E5_THREADS", os.cpu_count() or 1)))
print(f"E5 int8: motor {torch.backends.quantized.engine}, {torch.get_num_threads()} threads", flush=True)
modelo = torch.load(os.getenv("E5_MODELO", "/modelo/e5_int8.pt"), weights_only=False)
modelo.eval()

# Um cálculo de cada vez: dois em paralelo só disputariam os mesmos 2 vCPU.
_trava = threading.Lock()

app = FastAPI(title="Koma E5")


class PedidoEmbed(BaseModel):
    textos: List[str]


@app.get("/health")
def health():
    return {"ok": True, "dim": modelo.get_sentence_embedding_dimension()}


@app.post("/embed")
def embed(pedido: PedidoEmbed, x_e5_token: Optional[str] = Header(default=None)):
    if TOKEN and x_e5_token != TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido")
    if not pedido.textos or len(pedido.textos) > MAX_TEXTOS:
        raise HTTPException(status_code=422, detail=f"Envie entre 1 e {MAX_TEXTOS} textos")
    with _trava, torch.inference_mode():
        vetores = modelo.encode(pedido.textos, convert_to_numpy=True)
    return {"vetores": vetores.tolist()}
