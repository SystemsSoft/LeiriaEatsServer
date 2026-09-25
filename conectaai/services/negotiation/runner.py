# Arquivo: conectaai/services/negotiation/runner.py
#
# Não existe Celery/fila neste módulo. Uma negociação com Gemini real pode
# levar dezenas de segundos por turno (p99 documentado no Koma: ~55s) e
# várias rodadas — inviável dentro de um único request HTTP. A solução é
# FastAPI BackgroundTasks: a rota devolve 202 na hora e este runner continua
# processando turno a turno DEPOIS da resposta ter sido enviada.
#
# Cada turno é sua própria transação (engine.run_turn faz commits internos
# via repository), então um `systemctl restart` no meio (o deploy_conectaai.sh
# faz isso) perde no máximo o turno em voo — nunca corrompe os turnos já
# persistidos. A negociação fica com lease vencida, o sweep do startup
# devolve pra "queued", e /negotiations/{id}/resume retoma de onde parou.
#
# `_semaphore` limita quantas negociações rodam ao mesmo tempo neste
# processo — a t3.medium tem 2 vCPU e cada turno pode segurar uma thread
# por até a duração do deadline; sem isso, muitas negociações simultâneas
# saturariam o threadpool do FastAPI.
import threading
import uuid

from conectaai.core.config import settings
from conectaai.core.database import SessionLocal
from conectaai.repositories.negotiation_repo import NegotiationRepository
from conectaai.services.negotiation import engine
from conectaai.services.negotiation.state_machine import is_terminal

_semaphore = threading.Semaphore(settings.NEGOTIATION_MAX_CONCURRENT)


def run_negotiation(negotiation_id: str) -> None:
    """Ponto de entrada chamado por BackgroundTasks (e também podia ser
    chamado por um cron externo, se um dia isso virar um processo separado —
    não depende de estar dentro do mesmo request que o criou)."""
    acquired = _semaphore.acquire(blocking=False)
    if not acquired:
        # Processo já está ocupado no limite de negociações simultâneas.
        # A negociação fica em `queued`; o próximo GET/resume tenta de novo.
        return
    try:
        _drive_to_completion(negotiation_id)
    finally:
        _semaphore.release()


def _drive_to_completion(negotiation_id: str) -> None:
    lease_owner = uuid.uuid4().hex
    max_errors = 3

    while True:
        db = SessionLocal()
        try:
            negotiation = NegotiationRepository.acquire_lease(
                db, negotiation_id, lease_owner=lease_owner, lease_seconds=settings.NEGOTIATION_LEASE_SECONDS
            )
            if negotiation is None:
                return  # já terminal, ou outro worker está com o lease

            try:
                negotiation = engine.run_turn(db, negotiation)
            except Exception as exc:  # noqa: BLE001 — nunca deixar uma exceção matar o loop sem registrar
                db.rollback()  # a exceção pode ter deixado a sessão inválida (ex.: IntegrityError)
                negotiation.error_count = (negotiation.error_count or 0) + 1
                if negotiation.error_count >= max_errors:
                    negotiation.state = "failed"
                    negotiation.outcome = "failed"
                    negotiation.outcome_reason = f"Erro repetido no motor de negociação: {exc}"
                db.commit()
                if negotiation.state == "failed":
                    return
                # Devolve o lease antes de tentar de novo — sem isto a próxima volta
                # (e qualquer /resume) encontra a negociação "ocupada" por um turno
                # que já morreu, e ela fica presa em `running` até o lease vencer.
                NegotiationRepository.release_lease(db, negotiation)
                continue

            if is_terminal(negotiation.state):
                return

            NegotiationRepository.release_lease(db, negotiation)
        finally:
            db.close()
