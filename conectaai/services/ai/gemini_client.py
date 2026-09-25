# Arquivo: conectaai/services/ai/gemini_client.py
#
# Client Gemini com saída estruturada (JSON mode) e failover entre múltiplas
# chaves. Estilo de failover replicado de
# LeiriaEatsServer/services/gemini_sales_service.py (fora do módulo
# ConectaAí — não importado daqui, só usado como referência de padrão): 429
# pula pra próxima chave, 503 espera 0.5s e pula, tudo sob um deadline total
# por turno. A diferença deliberada: aqui sempre usamos response_schema, o
# Koma usa tags de texto.
#
# Se não houver nenhuma CONECTAAI_GEMINI_API_KEY configurada, generate_json devolve
# None imediatamente (sem tentar rede) — o motor de negociação
# (services/negotiation/engine.py) trata isso como "usar o agente
# determinístico", nunca como erro fatal.
import time
from typing import Optional, Tuple, Type, TypeVar

from pydantic import BaseModel, ValidationError

from conectaai.core.config import settings

T = TypeVar("T", bound=BaseModel)

_clients = None  # lista de genai.Client, uma por chave — inicializada sob demanda


def _get_clients():
    global _clients
    if _clients is not None:
        return _clients
    if not settings.GEMINI_API_KEYS:
        _clients = []
        return _clients
    from google import genai  # import local: evita custo de import em quem nunca usa IA

    _clients = [genai.Client(api_key=key) for key in settings.GEMINI_API_KEYS]
    return _clients


class GeminiCallResult:
    def __init__(self, parsed: Optional[BaseModel], raw_text: str, key_index: int, attempt: int, status: str, latency_ms: int, error: str = ""):
        self.parsed = parsed
        self.raw_text = raw_text
        self.key_index = key_index
        self.attempt = attempt
        self.status = status
        self.latency_ms = latency_ms
        self.error = error


def generate_json(
    *,
    system_instruction: str,
    user_content: str,
    response_model: Type[T],
    deadline_s: Optional[float] = None,
    max_retries: int = 2,
    temperature: float = 0.3,
) -> Optional[GeminiCallResult]:
    """Chama o Gemini pedindo JSON que já bate com `response_model` (JSON
    mode do SDK — response_schema). Devolve None só quando não há chave
    configurada; qualquer outro caminho devolve um GeminiCallResult (mesmo
    em erro), pra quem chamou decidir o que logar."""
    clients = _get_clients()
    if not clients:
        return None

    from google.genai import errors as genai_errors
    from google.genai import types

    deadline_s = deadline_s or settings.NEGOTIATION_TURN_DEADLINE_S
    deadline_at = time.monotonic() + deadline_s
    last_error = ""

    for attempt in range(1, max_retries + 2):  # tentativa inicial + max_retries
        for key_index, client in enumerate(clients):
            if time.monotonic() >= deadline_at:
                return GeminiCallResult(None, "", key_index, attempt, "timeout", int(deadline_s * 1000), "deadline excedido")

            start = time.monotonic()
            try:
                response = client.models.generate_content(
                    model=settings.GEMINI_MODEL,
                    contents=user_content,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=temperature,
                        max_output_tokens=600,
                        response_mime_type="application/json",
                        response_schema=response_model,
                    ),
                )
                latency_ms = int((time.monotonic() - start) * 1000)
                raw_text = response.text or ""
                try:
                    parsed = response_model.model_validate_json(raw_text)
                    return GeminiCallResult(parsed, raw_text, key_index, attempt, "ok", latency_ms)
                except ValidationError as ve:
                    last_error = f"invalid_json: {ve}"
                    # 1 retry corretivo específico de validação (não conta como troca de chave)
                    if attempt <= max_retries:
                        break  # sai do loop de chaves, tenta de novo no próximo `attempt`
                    return GeminiCallResult(None, raw_text, key_index, attempt, "invalid_json", latency_ms, last_error)

            except genai_errors.ClientError as e:
                latency_ms = int((time.monotonic() - start) * 1000)
                last_error = str(e)
                if "RESOURCE_EXHAUSTED" in last_error or "429" in last_error:
                    continue  # próxima chave, sem espera — é cota, não indisponibilidade
                if "UNAVAILABLE" in last_error or "503" in last_error:
                    time.sleep(0.5)
                    continue
                return GeminiCallResult(None, "", key_index, attempt, "error", latency_ms, last_error)
            except Exception as e:  # noqa: BLE001 — qualquer outra falha de rede/SDK também cai pro fallback
                latency_ms = int((time.monotonic() - start) * 1000)
                last_error = str(e)
                continue

    return GeminiCallResult(None, "", len(clients) - 1, max_retries + 1, "error", 0, last_error or "esgotou chaves e tentativas")
