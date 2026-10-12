# Arquivo: conectaai/services/instagram_service.py
#
# "Conectar Instagram" do perfil do creator: tudo o que o ConectaAI fala com o Instagram passa por aqui (os testes
# trocam `fetch_profile` por uma falsa).
#
# Modelo: API do Instagram com login do Instagram (contas profissionais — Business/Creator). O creator faz login
# na página do próprio Instagram e o app recebe um `code` de uso único; aqui esse código é trocado por um token
# (com a chave secreta do app, que só o servidor tem) e o token é usado UMA vez, para ler quem é a conta. Nem a
# senha nem o token ficam guardados: só o id, o @ e os seguidores, que provam que o perfil é da pessoa.
#
# `requests` não está no requirements.txt por nome, mas já é dependência do google-auth usado em core/firebase.py.
import logging
from dataclasses import dataclass

import requests

from conectaai.core.config import settings

logger = logging.getLogger("conectaai.instagram")

_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
_PROFILE_URL = "https://graph.instagram.com/me"
_TIMEOUT_S = 15

_REFUSED = "O Instagram não aceitou a autorização. Tente conectar de novo."
_OFFLINE = "Não consegui falar com o Instagram. Tente de novo em instantes."


class InstagramUnavailable(Exception):
    """O app do Instagram não está configurado neste servidor (falta a chave secreta)."""


class InstagramError(Exception):
    """O Instagram recusou a operação; a mensagem pode ser mostrada ao usuário."""


@dataclass
class InstagramProfile:
    user_id: str
    username: str
    followers: int


def is_configured() -> bool:
    return bool(settings.INSTAGRAM_APP_ID and settings.INSTAGRAM_APP_SECRET)


def _json(response) -> dict:
    try:
        body = response.json()
    except ValueError:
        body = {}
    return body if isinstance(body, dict) else {}


def _exchange_code(code: str, redirect_uri: str) -> str:
    """Troca o `code` (uso único, vale poucos minutos) pelo token de acesso de curta duração."""
    try:
        response = requests.post(
            _TOKEN_URL,
            data={
                "client_id": settings.INSTAGRAM_APP_ID,
                "client_secret": settings.INSTAGRAM_APP_SECRET,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                # o Instagram acrescenta "#_" ao fim do endereço de retorno; não faz parte do código
                "code": code.split("#")[0],
            },
            timeout=_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        logger.warning("Instagram fora do ar na troca do código: %s", type(exc).__name__)
        raise InstagramError(_OFFLINE)

    body = _json(response)
    # a resposta vem direta ou dentro de {"data": [...]}, conforme a versão da API
    data = body["data"][0] if isinstance(body.get("data"), list) and body["data"] else body
    token = data.get("access_token") if isinstance(data, dict) else None
    if response.status_code != 200 or not token:
        logger.warning("Instagram recusou o código (%s): %s", response.status_code, body.get("error_message") or body.get("error"))
        raise InstagramError(_REFUSED)
    return token


def fetch_profile(code: str, redirect_uri: str) -> InstagramProfile:
    """Conclui o login: troca o `code` pelo token e lê a conta que autorizou. O `redirect_uri` tem de ser o mesmo
    usado para abrir o login (e estar cadastrado no painel da Meta) — o Instagram confere."""
    if not is_configured():
        raise InstagramUnavailable()

    token = _exchange_code(code, redirect_uri)
    try:
        response = requests.get(
            _PROFILE_URL,
            params={"fields": "user_id,username,followers_count", "access_token": token},
            timeout=_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        logger.warning("Instagram fora do ar na leitura do perfil: %s", type(exc).__name__)
        raise InstagramError(_OFFLINE)

    body = _json(response)
    user_id = str(body.get("user_id") or body.get("id") or "")
    username = str(body.get("username") or "")
    if response.status_code != 200 or not user_id or not username:
        error = body.get("error")
        logger.warning("Instagram não devolveu o perfil (%s): %s", response.status_code, error.get("message") if isinstance(error, dict) else error)
        raise InstagramError(_REFUSED)
    return InstagramProfile(user_id=user_id, username=username, followers=int(body.get("followers_count") or 0))
