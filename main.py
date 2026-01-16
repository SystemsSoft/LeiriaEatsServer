from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from services.ai_service import AIService
from api import endpoints

app = FastAPI(title="Leria Eats - Modular Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(endpoints.router)
from core.database import SessionLocal


@app.on_event("startup")
async def startup_event():
    print("🚀 Servidor iniciando...")

    db = SessionLocal()
    try:
        print("🧠 Aquecendo a IA com dados da AWS...")
        AIService.initialize(db)
    finally:
        db.close()

@app.get("/")
def health_check():
    return {
        "status": "online",
        "message": "Sistema de Gestão Leria Eats (Arquitetura Modular) rodando! 🚀"
    }