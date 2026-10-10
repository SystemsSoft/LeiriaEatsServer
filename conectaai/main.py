# Arquivo: conectaai/main.py
#
# Entrypoint PRÓPRIO do módulo ConectaAI — processo FastAPI separado do Koma
# (main.py na raiz do repo), rodando em outra porta (8081 por padrão). Não
# importa nada de core/, api/, services/ do Koma: zero acoplamento em runtime.
# Rodar com:
#   uvicorn conectaai.main:app --host 0.0.0.0 --port 8081
import logging
import os
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from conectaai.core.config import settings
from conectaai.core.database import Base, engine
from conectaai.models import sql_models  # noqa: F401 — garante que os models sejam registrados na Base antes do create_all

from conectaai.api.routes import (
    agreement_routes,
    ai_routes,
    auth_routes,
    campaign_ai_routes,
    campaign_routes,
    collaboration_routes,
    company_routes,
    contract_routes,
    conversation_routes,
    creator_routes,
    favorite_routes,
    finance_routes,
    mandate_routes,
    match_routes,
    negotiation_routes,
    notification_routes,
    opportunity_routes,
    proposal_routes,
)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="ConectaAI Backend")

logger = logging.getLogger("conectaai")


# Precisa ser registrado ANTES do CORSMiddleware: o último middleware adicionado é o mais
# externo, então este fica por dentro do CORS. Sem ele, uma exceção não tratada (ex.: erro de
# banco) é respondida pelo ServerErrorMiddleware do Starlette, que fica FORA do CORS — a
# resposta 500 sai sem Access-Control-Allow-Origin e o navegador mostra "blocked by CORS
# policy" em vez do erro real. HTTPException continua sendo tratada normalmente (400, 404…).
@app.middleware("http")
async def unhandled_errors_as_json(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:  # noqa: BLE001 — qualquer falha inesperada vira 500 em JSON, com CORS
        error_id = uuid.uuid4().hex[:8]
        logger.exception("Erro não tratado em %s %s (id %s)", request.method, request.url.path, error_id)
        return JSONResponse(
            status_code=500,
            content={"detail": "Erro interno do servidor. Tente novamente em instantes.", "error_id": error_id},
        )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_routes.router)
app.include_router(company_routes.router)
app.include_router(creator_routes.router)
app.include_router(campaign_routes.router)
app.include_router(proposal_routes.router)
app.include_router(conversation_routes.router)
app.include_router(notification_routes.router)
app.include_router(opportunity_routes.router)
app.include_router(favorite_routes.router)
app.include_router(ai_routes.router)
app.include_router(mandate_routes.router)
app.include_router(negotiation_routes.router)
app.include_router(agreement_routes.router)
app.include_router(campaign_ai_routes.router)
app.include_router(match_routes.router)
app.include_router(contract_routes.router)
app.include_router(collaboration_routes.router)
app.include_router(collaboration_routes.deliverables_router)
app.include_router(finance_routes.router)

os.makedirs(os.path.join(settings.UPLOAD_DIR, "avatars"), exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")


@app.on_event("startup")
def _sweep_stuck_negotiations():
    """Qualquer negociação presa em `running` com lease vencido é de um
    processo anterior que morreu no meio (ex.: restart do deploy_conectaai.sh)
    — devolve pra `queued` para ser retomada no próximo /resume ou GET."""
    from conectaai.core.database import SessionLocal
    from conectaai.repositories.negotiation_repo import NegotiationRepository

    db = SessionLocal()
    try:
        NegotiationRepository.sweep_expired_leases(db)
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok", "service": "conectaai"}
