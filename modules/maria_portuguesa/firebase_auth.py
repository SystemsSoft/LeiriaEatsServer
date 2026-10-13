# Arquivo: modules/maria_portuguesa/firebase_auth.py
#
# Verificação dos ID tokens do Firebase Authentication (projeto da Maria Portuguesa).
# O frontend cria a conta e inicia sessão no Firebase; aqui só se confirma que o
# token foi mesmo emitido pelo Firebase para esse projeto. Não precisa de conta de
# serviço: a assinatura é verificada com as chaves públicas da Google.
#
# Variáveis de ambiente (opcionais):
#   MP_FIREBASE_PROJECT_ID   projeto Firebase (por omissão: mariaportuguesa)
import os
from dataclasses import dataclass
from typing import Callable, Optional

MP_FIREBASE_PROJECT_ID = os.getenv("MP_FIREBASE_PROJECT_ID", "mariaportuguesa")


@dataclass(frozen=True)
class ContaFirebase:
    uid: str
    email: str
    email_verificado: bool


class TokenFirebaseInvalido(Exception):
    pass


class VerificadorFirebase:
    # Substituível nos testes, que não têm tokens verdadeiros.
    _verificador: Optional[Callable[[str], ContaFirebase]] = None

    @classmethod
    def definir_verificador(cls, verificador: Optional[Callable[[str], ContaFirebase]]) -> None:
        cls._verificador = verificador

    @classmethod
    def verificar(cls, id_token: str) -> ContaFirebase:
        """Devolve a conta a que o token pertence; TokenFirebaseInvalido se o token
        estiver expirado, adulterado ou for de outro projeto."""
        return (cls._verificador or cls._verificar_na_google)(id_token)

    @staticmethod
    def _verificar_na_google(id_token: str) -> ContaFirebase:
        # Import tardio, como no resto do módulo.
        from google.auth.transport import requests as transporte
        from google.oauth2 import id_token as tokens

        try:
            dados = tokens.verify_firebase_token(id_token, transporte.Request(), audience=MP_FIREBASE_PROJECT_ID)
        except Exception as e:
            raise TokenFirebaseInvalido(str(e)) from e
        if not dados or dados.get("iss") != f"https://securetoken.google.com/{MP_FIREBASE_PROJECT_ID}":
            raise TokenFirebaseInvalido("emissor inesperado")
        uid, email = dados.get("sub"), (dados.get("email") or "").strip().lower()
        if not uid or not email:
            raise TokenFirebaseInvalido("token sem utilizador ou sem email")
        return ContaFirebase(uid=uid, email=email, email_verificado=bool(dados.get("email_verified")))
