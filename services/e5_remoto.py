# Arquivo: services/e5_remoto.py
"""Cliente do serviço de embeddings E5 (e5_service/, na instância dedicada).

Imita a parte do SentenceTransformer que o AIService usa — encode(textos,
convert_to_tensor=True) — para o resto da pesquisa (limiares, busca híbrida, cache
por restaurante) não mudar uma linha. AIService.get_model() devolve este cliente
quando E5_SERVICE_URL está definido no .env, e a API deixa de carregar o modelo.

Os vetores dos produtos ("passage: ...") ficam em cache no disco da API, por modelo
(services/cache_vetores.py): num arranque só os produtos novos ou alterados vão ao
serviço. Antes, cada arranque recalculava o catálogo inteiro e esgotava os créditos de
CPU da instância do E5 (Lightsail "burstable", 05/11/2026).
"""
import json
import threading
import urllib.error
import urllib.request

import numpy as np
import torch

from services.cache_vetores import CacheVetores

# O int8 em x86 leva ~0,3 s (texto curto) a ~1,8 s (descrição longa) por texto num lote, e
# mais quando a instância do E5 fica sem créditos de CPU. Lotes de 4 ficam bem abaixo do
# timeout mesmo assim, e um pedido abandonado desperdiça pouco trabalho no serviço.
_LOTE = 4
_TIMEOUT_PESQUISA = 15.0
_PREFIXO_CACHE = "passage: "


class E5Indisponivel(RuntimeError):
    """O motor de embeddings não respondeu — o AIService cai para a pesquisa só léxica."""


class E5Remoto:
    def __init__(self, url: str, token: str = "", timeout: float = 60.0, pasta_cache: str = ""):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.pasta_cache = pasta_cache
        self._cache = None  # CacheVetores, aberta no 1º uso (precisa do id do modelo)
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

    def _abrir_cache(self):
        """Cache por modelo: o /health do serviço diz qual é, e um modelo novo começa vazio."""
        if self._cache is None:
            id_modelo = self._abrir(f"{self.url}/health", _TIMEOUT_PESQUISA).get("modelo", "sem-id")
            self._cache = CacheVetores(self.pasta_cache, id_modelo)
        return self._cache

    def encode(self, sentences, convert_to_tensor: bool = False, **_):
        unico = isinstance(sentences, str)
        textos = [sentences] if unico else list(sentences)
        # A pesquisa de um utilizador (1 texto, ~0,3 s) não espera o timeout dos lotes de
        # indexação: se o serviço estiver preso, cai para o plano B léxico em 15 s.
        timeout = min(self.timeout, _TIMEOUT_PESQUISA) if unico else self.timeout

        usar_cache = bool(self.pasta_cache) and any(t.startswith(_PREFIXO_CACHE) for t in textos)
        vetores = [None] * len(textos)
        with self._trava:
            cache = self._abrir_cache() if usar_cache else None
            if cache is not None:
                for i, t in enumerate(textos):
                    if t.startswith(_PREFIXO_CACHE):
                        vetores[i] = cache.get(t)
            em_falta = [i for i, v in enumerate(vetores) if v is None]
            for inicio in range(0, len(em_falta), _LOTE):
                lote = em_falta[inicio:inicio + _LOTE]
                novos = False
                for i, vetor in zip(lote, self._pedir([textos[i] for i in lote], timeout)):
                    vetores[i] = np.asarray(vetor, dtype=np.float32)
                    if cache is not None and textos[i].startswith(_PREFIXO_CACHE):
                        cache.put(textos[i], vetores[i])
                        novos = True
                if novos:
                    # Grava a cada lote: se o serviço cair a meio, o que já veio não se perde.
                    cache.gravar()

        matriz = np.stack(vetores).astype(np.float32)
        if unico:
            matriz = matriz[0]
        return torch.from_numpy(matriz) if convert_to_tensor else matriz

    def get_sentence_embedding_dimension(self) -> int:
        return len(self.encode("query: teste"))
