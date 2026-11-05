# Arquivo: services/e5_remoto.py
"""Cliente do serviço de embeddings E5 (e5_service/, na instância dedicada).

Imita a parte do SentenceTransformer que o AIService usa — encode(textos,
convert_to_tensor=True) — para o resto da pesquisa (limiares, busca híbrida, cache
por restaurante) não mudar uma linha. AIService.get_model() devolve este cliente
quando E5_SERVICE_URL está definido no .env, e a API deixa de carregar o modelo.
"""
import json
import urllib.error
import urllib.request

import numpy as np
import torch

# O int8 em x86 leva ~0,3 s (texto curto) a ~1,7 s (descrição longa) por texto num lote:
# 37 produtos num só pedido passavam do timeout no arranque e o índice ficava vazio
# (05/11/2026). Lotes de 8 ficam em ~15 s no pior caso, e um pedido abandonado pelo
# timeout desperdiça pouco trabalho no serviço.
_LOTE = 8


class E5Indisponivel(RuntimeError):
    """O serviço de embeddings não respondeu — o AIService cai para a pesquisa só léxica."""


class E5Remoto:
    def __init__(self, url: str, token: str = "", timeout: float = 60.0):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _pedir(self, textos):
        corpo = json.dumps({"textos": textos}).encode("utf-8")
        pedido = urllib.request.Request(f"{self.url}/embed", data=corpo, headers={
            "Content-Type": "application/json",
            "X-E5-Token": self.token,
        })
        try:
            with urllib.request.urlopen(pedido, timeout=self.timeout) as resposta:
                return json.load(resposta)["vetores"]
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as e:
            raise E5Indisponivel(str(e)) from e

    def encode(self, sentences, convert_to_tensor: bool = False, **_):
        unico = isinstance(sentences, str)
        textos = [sentences] if unico else list(sentences)
        vetores = []
        for i in range(0, len(textos), _LOTE):
            vetores.extend(self._pedir(textos[i:i + _LOTE]))
        matriz = np.asarray(vetores, dtype=np.float32)
        if unico:
            matriz = matriz[0]
        return torch.from_numpy(matriz) if convert_to_tensor else matriz

    def get_sentence_embedding_dimension(self) -> int:
        return len(self.encode("query: teste"))
