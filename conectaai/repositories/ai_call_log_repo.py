# Arquivo: conectaai/repositories/ai_call_log_repo.py
import hashlib
from typing import Optional

from sqlalchemy.orm import Session

from conectaai.models.sql_models import AiCallLogDB

_RESPONSE_TRUNCATE_CHARS = 8000


class AiCallLogRepository:
    @staticmethod
    def get_by_id(db: Session, log_id: str) -> Optional[AiCallLogDB]:
        return db.query(AiCallLogDB).filter(AiCallLogDB.id == log_id).first()

    @staticmethod
    def create(
        db: Session,
        *,
        negotiation_id: str = None,
        turn_id: str = None,
        purpose: str,
        model: str,
        key_index: int,
        attempt: int,
        status: str,
        latency_ms: int,
        prompt: str,
        response_raw: str = "",
        error: str = "",
    ) -> AiCallLogDB:
        # Nunca grava o prompt em texto claro (contém perfil de terceiro:
        # bio, cidade, categorias do creator) — só o hash, para conseguir
        # comparar "esse mesmo prompt gerou respostas diferentes?" sem expor
        # dado pessoal no log.
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        log = AiCallLogDB(
            negotiation_id=negotiation_id,
            turn_id=turn_id,
            purpose=purpose,
            model=model,
            key_index=key_index,
            attempt=attempt,
            status=status,
            latency_ms=latency_ms,
            prompt_hash=prompt_hash,
            prompt_chars=len(prompt),
            response_raw=(response_raw or "")[:_RESPONSE_TRUNCATE_CHARS],
            error=(error or "")[:2000],
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log
