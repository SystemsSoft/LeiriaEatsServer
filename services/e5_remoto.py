# Arquivo: services/e5_remoto.py
"""Cliente do serviço de embeddings E5 (e5_service/, na instância dedicada).

Imita a parte do SentenceTransformer que o AIService usa — encode(textos,
convert_to_tensor=True) — para o resto da pesquisa (limiares, busca híbrida, cache
por restaurante) não mudar uma linha. AIService.get_model() devolve este cliente
quando E5_SERVICE_URL está definido no .env, e a API deixa de carregar o modelo.

Os vetores dos produtos ("passage: ...") ficam em cache no disco da API, por modelo:
num arranque só os produtos novos ou alterados vão ao serviço. Antes, cada arranque
recalculava o catálogo inteiro e esgotava os créditos de CPU da instância do E5
(Lightsail "burstable", 05/11/2026).
"""
import hashlib
import json
import os
import pickle
import threading
import urllib.error
import urllib.request

import numpy as np
import torch

# O int8 em x86 leva ~0,3 s (texto curto) a ~1,8 s (descrição longa) por texto num lote, e
# mais quando a instância do E5 fica sem créditos de CPU. Lotes de 4 ficam bem abaixo do
# timeout mesmo assim, e um pedido abandonado desperdiça pouco trabalho no serviço.
_LOTE = 4
_TIMEOUT_PESQUISA = 15.0
_PREFIXO_CACHE = "passage: "


class E5Indisponivel(RuntimeError):
    """O serviço de embeddings não respondeu — o AIService cai para a pesquisa só léxica."""


class E5Remoto:
    def __init__(self, url: str, token: str = "", timeout: float = 60.0, pasta_cache: str = ""):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.pasta_cache = pasta_cache
        self._cache = None  # {sha256(texto): vetor}, carregado no 1º uso
        self._ficheiro_cache = None
        self._trava = threading.Lock()

    def _abrir(self, pedido, timeout):
        try:
            with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
                return json.load(resposta)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise E5Indisponivel(str(e)) from e

    def _pedir(self, textos, timeout):
        corpo = json.dumps({"textos": textos}).encode("utf-8")
        pedido = urllib.request.Request(f"{self.url}/embed", data=corpo, headers={
            "Content-Type": "application/json",
            "X-E5-Token": self.token,
        })
        try:
            return self._abrir(pedido, timeout)["vetores"]
        except KeyError as e:
            raise E5Indisponivel(str(e)) from e

    def _carregar_cache(self):
        """Cache por modelo: o /health do serviço diz qual é, e um modelo novo começa vazio."""
        if self._cache is not None:
            return
        id_modelo = self._abrir(f"{self.url}/health", _TIMEOUT_PESQUISA).get("modelo", "sem-id")
        self._ficheiro_cache = os.path.join(self.pasta_cache, f"vetores-{id_modelo}.pkl")
        try:
            with open(self._ficheiro_cache, "rb") as f:
                self._cache = pickle.load(f)
        except (OSError, pickle.UnpicklingError, EOFError):
            self._cache = {}

    def _gravar_cache(self):
        os.makedirs(self.pasta_cache, exist_ok=True)
        temporario = f"{self._ficheiro_cache}.tmp"
        with open(temporario, "wb") as f:
            pickle.dump(self._cache, f)
        os.replace(temporario, self._ficheiro_cache)

    def encode(self, sentences, convert_to_tensor: bool = False, **_):
        unico = isinstance(sentences, str)
        textos = [sentences] if unico else list(sentences)
        # A pesquisa de um utilizador (1 texto, ~0,3 s) não espera o timeout dos lotes de
        # indexação: se o serviço estiver preso, cai para o plano B léxico em 15 s.
        timeout = min(self.timeout, _TIMEOUT_PESQUISA) if unico else self.timeout

        usar_cache = bool(self.pasta_cache) and any(t.startswith(_PREFIXO_CACHE) for t in textos)
        vetores = [None] * len(textos)
        with self._trava:
            if usar_cache:
                self._carregar_cache()
                chaves = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in textos]
                for i, chave in enumerate(chaves):
                    if textos[i].startswith(_PREFIXO_CACHE) and chave in self._cache:
                        vetores[i] = self._cache[chave]
            em_falta = [i for i, v in enumerate(vetores) if v is None]
            novos = 0
            for inicio in range(0, len(em_falta), _LOTE):
                lote = em_falta[inicio:inicio + _LOTE]
                for i, vetor in zip(lote, self._pedir([textos[i] for i in lote], timeout)):
                    vetores[i] = np.asarray(vetor, dtype=np.float32)
                    if usar_cache and textos[i].startswith(_PREFIXO_CACHE):
                        self._cache[chaves[i]] = vetores[i]
                        novos += 1
                if novos:
                    # Grava a cada lote: se o serviço cair a meio, o que já veio não se perde.
                    self._gravar_cache()

        matriz = np.stack(vetores).astype(np.float32)
        if unico:
            matriz = matriz[0]
        return torch.from_numpy(matriz) if convert_to_tensor else matriz

    def get_sentence_embedding_dimension(self) -> int:
        return len(self.encode("query: teste"))
