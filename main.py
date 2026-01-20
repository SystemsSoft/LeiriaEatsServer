# Arquivo: main.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from core.database import Base, engine, SessionLocal
from services.ai_service import AIService
import os

# --- IMPORTANTE: Importe as rotas específicas ---
# Certifique-se que product_routes e search_routes estão na pasta api/routes
from api.routes import product_routes, search_routes, chat_routes, order_routes

# Cria as tabelas se não existirem
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Leria Eats - Modular Backend")

# Configuração de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuração de Imagens (Static)
if not os.path.exists("static/images"):
    os.makedirs("static/images")
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(product_routes.router)
app.include_router(search_routes.router)
app.include_router(chat_routes.router)
app.include_router(order_routes.router)

@app.on_event("startup")
async def startup_event():
    print("🚀 Iniciando Leiria Eats Server...")
    db = SessionLocal()
    try:
        # Carrega a IA com os dados do banco (Pizzaria Dom Bosco, etc)
        AIService.reload_data(db)
    except Exception as e:
        print(f"⚠️ Erro ao pré-carregar IA: {e}")
    finally:
        db.close()

@app.get("/")
def health_check():
    return {
        "status": "online",
        "message": "Sistema de Gestão Leria Eats rodando! 🚀"
    }