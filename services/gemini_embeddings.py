# Arquivo: services/gemini_embeddings.py
"""Embeddings do Gemini (gemini-embedding-001) como motor da pesquisa semântica.

Mesma interface que o AIService usa do E5 — encode(textos, convert_to_tensor=True) —
com os prefixos do E5 traduzidos para os tipos de tarefa do Gemini: "query: X" vira
RETRIEVAL_QUERY e "passage: X" vira RETRIEVAL_DOCUMENT. Ativo com EMBEDDINGS_GEMINI=true.

Medido em 06/11/2026 no tests/avaliacao_pesquisa.json: 37/40 no 1º lugar (E5 float32:
33/40, E5 int8 em produção: 34/40), por ~US$ 0,0007 o catálogo + 40 pesquisas. As
pontuações ficam noutra escala (média 0,615 contra 0,778 do E5): ajustar_limiar converte
os limiares fixos afinados para o E5 (ver AIService._limiar).
"""
import threading

import numpy as np
import torch
from google import genai
from google.genai import types

from services.cache_vetores import CacheVetores
from services.e5_remoto import E5Indisponivel

MODELO = "gemini-embedding-001"
_LOTE = 100  # máximo de textos por pedido à API
_TIMEOUT_MS = 15_000
_TIPOS = {"query: ": "RETRIEVAL_QUERY", "passage: ": "RETRIEVAL_DOCUMENT"}

# Escala do Gemini -> escala do E5, ajustada pela média e desvio das pontuações pesquisa ×
# produto das 40 pesquisas do conjunto de avaliação (E5 0,778 ± 0,032; Gemini 0,615 ± 0,038).
_ESCALA = 0.032 / 0.038
_DESLOCAMENTO = 0.778 - _ESCALA * 0.615


class GeminiEmbeddings:
    def __init__(self, api_key: str, pasta_cache: str = ""):
        # Sem repetições automáticas do SDK: com elas uma pesquisa chegou a ~45 s (3 × 15 s).
        # Um pedido falhado cai logo no plano B léxico do AIService.
        self.cliente = genai.Client(api_key=api_key, http_options=types.HttpOptions(
            timeout=_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)))
        self._cache = CacheVetores(pasta_cache, f"gemini-{MODELO}") if pasta_cache else None
        self._trava = threading.Lock()

    @staticmethod
    def ajustar_limiar(limiar_e5: float) -> float:
        """Limiar afinado para o E5 -> mesmo ponto na escala de pontuações do Gemini."""
        return (limiar_e5 - _DESLOCAMENTO) / _ESCALA

    @staticmethod
    def _tipo(texto: str):
        for prefixo, tipo in _TIPOS.items():
            if texto.startswith(prefixo):
                return tipo, texto[len(prefixo):]
        return "RETRIEVAL_DOCUMENT", texto

    def _pedir(self, tipo: str, textos: list) -> list:
        try:
            resposta = self.cliente.models.embed_content(
                model=MODELO, contents=textos, config=types.EmbedContentConfig(task_type=tipo))
            return [np.asarray(e.values, dtype=np.float32) for e in resposta.embeddings]
        except Exception as e:  # noqa: BLE001 — qualquer falha da API cai no plano B léxico
            raise E5Indisponivel(f"Gemini: {e}") from e

    def encode(self, sentences, convert_to_tensor: bool = False, **_):
        unico = isinstance(sentences, str)
        textos = [sentences] if unico else list(sentences)
        vetores = [None] * len(textos)
        # A trava protege só a cache. A chamada à API fica fora dela: com a trava à volta
        # de tudo as pesquisas faziam fila (~3,6/s com 32 clientes, medido em 06/11/2026),
        # quando cada uma é só uma espera de rede de ~0,3 s.
        if self._cache is not None:
            with self._trava:
                for i, t in enumerate(textos):
                    if t.startswith("passage: "):
                        vetores[i] = self._cache.get(t)
        por_tipo = {}
        for i, t in enumerate(textos):
            if vetores[i] is None:
                tipo, conteudo = self._tipo(t)
                por_tipo.setdefault(tipo, []).append((i, conteudo))
        novos = []
        for tipo, itens in por_tipo.items():
            for inicio in range(0, len(itens), _LOTE):
                lote = itens[inicio:inicio + _LOTE]
                for (i, _), vetor in zip(lote, self._pedir(tipo, [c for _, c in lote])):
                    vetores[i] = vetor
                    if self._cache is not None and textos[i].startswith("passage: "):
                        novos.append((textos[i], vetor))
        if novos:
            with self._trava:
                for texto, vetor in novos:
                    self._cache.put(texto, vetor)
                self._cache.gravar()

        matriz = np.stack(vetores).astype(np.float32)
        if unico:
            matriz = matriz[0]
        return torch.from_numpy(matriz) if convert_to_tensor else matriz

    def get_sentence_embedding_dimension(self) -> int:
        return len(self.encode("query: teste"))
