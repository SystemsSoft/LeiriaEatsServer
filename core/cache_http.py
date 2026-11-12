# Arquivo: core/cache_http.py
"""Respostas JSON com ETag: quando o cliente já tem a mesma versão, devolve 304 sem corpo.

O telemóvel guarda a resposta e manda o ETag em If-None-Match; se nada mudou, recebe só
"304 Not Modified" (poucos bytes) em vez da lista outra vez. "no-cache" obriga o cliente a
perguntar sempre — nunca mostra uma lista desatualizada, só poupa o download.
"""
import hashlib
import json

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response


def resposta_com_etag(request: Request, dados) -> Response:
    corpo = json.dumps(jsonable_encoder(dados), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    etag = '"' + hashlib.md5(corpo).hexdigest() + '"'
    cabecalhos = {"ETag": etag, "Cache-Control": "no-cache"}
    pedido = request.headers.get("if-none-match", "")
    if etag in [e.strip().removeprefix("W/") for e in pedido.split(",")]:
        return Response(status_code=304, headers=cabecalhos)
    return Response(content=corpo, media_type="application/json", headers=cabecalhos)

