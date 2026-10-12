# Arquivo: modules/maria_portuguesa/security.py
#
# Autenticação das domésticas. Só biblioteca padrão: scrypt para a palavra-passe e
# tokens opacos guardados em mp_sessoes (por hash).
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from modules.maria_portuguesa.models import MpDomesticaDB, MpSessaoDB

VALIDADE_SESSAO = timedelta(days=30)

_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1


def _agora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def gerar_hash_senha(senha: str) -> str:
    sal = secrets.token_bytes(16)
    derivada = hashlib.scrypt(senha.encode("utf-8"), salt=sal, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return "scrypt${}${}".format(
        base64.b64encode(sal).decode("ascii"),
        base64.b64encode(derivada).decode("ascii"),
    )


def verificar_senha(senha: str, senha_hash: str) -> bool:
    try:
        esquema, sal_b64, derivada_b64 = senha_hash.split("$")
        if esquema != "scrypt":
            return False
        sal = base64.b64decode(sal_b64)
        esperada = base64.b64decode(derivada_b64)
    except (ValueError, TypeError):
        return False
    derivada = hashlib.scrypt(senha.encode("utf-8"), salt=sal, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return hmac.compare_digest(derivada, esperada)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def criar_sessao(db: Session, domestica: MpDomesticaDB) -> str:
    """Cria a sessão e devolve o token em claro — é a única vez que ele existe fora
    do dispositivo da pessoa."""
    token = secrets.token_urlsafe(32)
    db.add(MpSessaoDB(
        token_hash=_hash_token(token),
        domestica_id=domestica.id,
        expira_em=_agora() + VALIDADE_SESSAO,
    ))
    db.commit()
    return token


def encerrar_sessao(db: Session, token: str) -> None:
    db.query(MpSessaoDB).filter(MpSessaoDB.token_hash == _hash_token(token)).delete()
    db.commit()


def _extrair_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sessão em falta. Inicie sessão novamente.")
    return authorization[7:].strip()


def token_atual(authorization: Optional[str] = Header(default=None)) -> str:
    return _extrair_token(authorization)


def domestica_atual(
        token: str = Depends(token_atual),
        db: Session = Depends(get_db),
) -> MpDomesticaDB:
    sessao = db.query(MpSessaoDB).filter(MpSessaoDB.token_hash == _hash_token(token)).first()
    if sessao is None or sessao.expira_em < _agora():
        raise HTTPException(status_code=401, detail="Sessão expirada. Inicie sessão novamente.")
    return sessao.domestica
