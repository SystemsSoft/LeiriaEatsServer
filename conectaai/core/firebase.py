# Arquivo: conectaai/core/firebase.py
#
# Verificação de ID token do Google/Firebase SEM precisar de credencial de
# service account: `google.oauth2.id_token.verify_firebase_token` confere a
# assinatura do JWT contra as chaves públicas do Google e o campo `aud`
# (= FIREBASE_PROJECT_ID) por conta própria. O Firebase Admin SDK (usado no
# Koma só para push FCM, ver services/push_notification_service.py na raiz do
# repo) não é reaproveitado aqui de propósito — aquela credencial pertence ao
# processo do Koma, não necessariamente ao mesmo projeto Firebase do ConectaAI.
from fastapi import HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from conectaai.core.config import settings

_request = google_requests.Request()


def verify_google_id_token(token: str) -> dict:
    try:
        claims = google_id_token.verify_firebase_token(token, _request, audience=settings.FIREBASE_PROJECT_ID)
    except Exception:
        claims = None

    if not claims or not claims.get("email"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token do Google inválido ou expirado"
        )
    return claims
