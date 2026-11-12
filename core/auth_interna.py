# Arquivo: core/auth_interna.py
"""Token interno para rotas de operação (reindexar a pesquisa, ver o estado detalhado da IA).

O token vem de ADMIN_TOKEN no .env do servidor e é enviado no cabeçalho X-Admin-Token.
Sem ADMIN_TOKEN configurado, estas rotas ficam fechadas (401) — nunca abertas por omissão.
"""
import hmac
from typing import Optional

from fastapi import Header, HTTPException

from core.config import settings


def token_interno_valido(token: Optional[str]) -> bool:
    return bool(settings.ADMIN_TOKEN) and bool(token) and hmac.compare_digest(token, settings.ADMIN_TOKEN)


def exigir_token_interno(x_admin_token: Optional[str] = Header(None)):
    if not token_interno_valido(x_admin_token):
        raise HTTPException(status_code=401, detail="Token interno em falta ou inválido")
