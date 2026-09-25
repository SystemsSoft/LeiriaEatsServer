# Arquivo: conectaai/scripts/check_gemini_key.py
#
# Confere se o módulo ConectaAI está usando a chave Gemini PRÓPRIA dele
# (CONECTAAI_GEMINI_API_KEY) e se ela funciona: uma chamada de geração e uma de
# embedding, pelos mesmos caminhos de código da API. Serve porque o módulo engole
# qualquer erro do Gemini e cai no agente determinístico / busca por palavra-chave
# — uma chave errada passaria despercebida no app.
#
# Rodar da raiz do repo do servidor:
#   .venv/bin/python -m conectaai.scripts.check_gemini_key
import sys

from pydantic import BaseModel

from conectaai.core.config import settings
from conectaai.services.ai import embeddings, gemini_client


class _Ping(BaseModel):
    ok: bool


def _mask(key: str) -> str:
    return f"{key[:4]}…{key[-4:]}" if len(key) > 8 else "****"


def main() -> int:
    if not settings.GEMINI_API_KEYS:
        print("✗ CONECTAAI_GEMINI_API_KEY não está configurada no .env — a IA do ConectaAI fica desativada.")
        return 1

    print(f"Chave(s) do ConectaAI em uso: {', '.join(_mask(k) for k in settings.GEMINI_API_KEYS)}")

    print(f"\n[geração] modelo {settings.GEMINI_MODEL}")
    result = gemini_client.generate_json(
        system_instruction="Responda apenas com JSON.",
        user_content='Responda {"ok": true}.',
        response_model=_Ping,
        deadline_s=20,
        max_retries=0,
    )
    gen_ok = result is not None and result.status == "ok"
    if gen_ok:
        print(f"  ✓ ok em {result.latency_ms} ms (chave #{result.key_index})")
    else:
        print(f"  ✗ {result.status if result else 'sem cliente'}: {result.error if result else ''}")

    print(f"\n[embedding] modelo {embeddings.MODEL_NAME}")
    vectors = embeddings.embed_texts(["teste de chave"], task_type="RETRIEVAL_QUERY")
    emb_ok = bool(vectors)
    print(f"  {'✓ ok' if emb_ok else '✗ falhou (a função engole o erro — veja cota/permissão da chave no AI Studio)'}")

    return 0 if gen_ok and emb_ok else 1


if __name__ == "__main__":
    sys.exit(main())
