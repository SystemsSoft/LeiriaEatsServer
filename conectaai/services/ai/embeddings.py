# Arquivo: conectaai/services/ai/embeddings.py
#
# Embeddings via API do Gemini (gemini-embedding-001) — nenhum modelo local
# neste processo. A t3.medium já roda o e5-large do Koma (raiz) usando boa
# parte da RAM disponível; carregar um segundo modelo aqui estouraria a
# máquina (ver PLANO_REFATORACAO_PERFORMANCE_IA.md do Koma, item 4.2). Por
# isso a semântica do ConectaAí é 100% via API, sem sentence-transformers.
#
# Falha (sem chave, erro de rede, quota) devolve None — nunca lança. Quem
# chama (services/matching_service.py) trata None como "sem semântica
# disponível agora" e cai no matching por palavra-chave, nunca quebra a
# busca do usuário.
import math
from typing import List, Optional

from conectaai.core.config import settings

MODEL_NAME = settings.GEMINI_EMBEDDING_MODEL
_EMBEDDING_DIM = 768

_clients = None


def _get_clients():
    global _clients
    if _clients is not None:
        return _clients
    if not settings.GEMINI_API_KEYS:
        _clients = []
        return _clients
    from google import genai

    _clients = [genai.Client(api_key=k) for k in settings.GEMINI_API_KEYS]
    return _clients


def available() -> bool:
    return bool(settings.GEMINI_API_KEYS)


def _normalize(vec: List[float]) -> List[float]:
    # A API pode truncar a dimensão (MRL) sem renormalizar o vetor — sem
    # isso, a similaridade de cosseno calculada depois fica incorreta.
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def embed_texts(texts: List[str], *, task_type: str = "RETRIEVAL_DOCUMENT") -> Optional[List[List[float]]]:
    """`task_type` é o mecanismo oficial do Gemini para diferenciar
    'documento a ser indexado' de 'consulta de busca' — ao contrário do E5
    (Koma), aqui não é um prefixo de texto, é um parâmetro da chamada."""
    clients = _get_clients()
    if not clients or not texts:
        return None

    from google.genai import types

    for client in clients:
        try:
            response = client.models.embed_content(
                model=MODEL_NAME,
                contents=texts,
                config=types.EmbedContentConfig(output_dimensionality=_EMBEDDING_DIM, task_type=task_type),
            )
            return [_normalize(list(e.values)) for e in response.embeddings]
        except Exception:  # noqa: BLE001 — tenta a próxima chave; se todas falharem, None
            continue
    return None
