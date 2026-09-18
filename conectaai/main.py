# Arquivo: conectaai/main.py
#
# Entrypoint PRÓPRIO do módulo ConectaAI — processo FastAPI separado do Koma
# (main.py na raiz do repo), rodando em outra porta (8081 por padrão). Não
# importa nada de core/, api/, services/ do Koma: zero acoplamento em runtime.
# Rodar com:
#   uvicorn conectaai.main:app --host 0.0.0.0 --port 8081
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from conectaai.core.config import settings
from conectaai.core.database import Base, engine
from conectaai.models import sql_models  # noqa: F401 — garante que os models sejam registrados na Base antes do create_all

from conectaai.api.routes import (
    ai_routes,
    auth_routes,
    campaign_routes,
    company_routes,
    conversation_routes,
    creator_routes,
    favorite_routes,
    notification_routes,
    opportunity_routes,
    proposal_routes,
)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="ConectaAI Backend")

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

os.makedirs(os.path.join(settings.UPLOAD_DIR, "avatars"), exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")


@app.get("/health")
def health():
    return {"status": "ok", "service": "conectaai"}
