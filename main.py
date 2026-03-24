# Arquivo: main.py
import asyncio

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import FileResponse

from core.database import Base, engine, SessionLocal
from services.ai_service import AIService
import os


from api.routes import product_routes, search_routes, chat_routes, order_routes, auth_routes, upload_routes, \
    company_routes
from api.routes import drivers as driver_routes
from services.courier_notification_service import courier_notification_worker

# Cria as tabelas se não existirem
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Leria Eats - Modular Backend")


# ──────────────────────────────────────────────────────────────
# Handler global de erros de validação (422)
# ──────────────────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    erros_formatados = []
    for erro in exc.errors():
        # loc = ("body", "login") → "body → login"
        caminho = " → ".join(str(parte) for parte in erro.get("loc", []))
        mensagem = erro.get("msg", "Erro desconhecido")
        tipo = erro.get("type", "")
        valor_recebido = erro.get("input", "—")

        erros_formatados.append({
            "campo":    caminho,
            "problema": mensagem,
            "tipo":     tipo,
            "recebido": str(valor_recebido),
        })

    # Imprime no terminal para facilitar o debug
    print("❌ Erro 422 – Falha na validação do pedido:")
    print(f"   Rota: {request.method} {request.url.path}")
    for e in erros_formatados:
        print(f"   • [{e['campo']}] {e['problema']} (tipo={e['tipo']}, recebido={e['recebido']})")

    return JSONResponse(
        status_code=422,
        content={
            "erro":    "Dados inválidos ou em falta no pedido.",
            "detalhes": erros_formatados,
        },
    )

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
app.include_router(auth_routes.router)
app.include_router(upload_routes.router)
app.include_router(company_routes.router)
app.include_router(driver_routes.router)   # /drivers/*

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

    # Inicia o worker de notificação a estafetas em background
    asyncio.create_task(courier_notification_worker())

app.mount("/assets", StaticFiles(directory="static/assets"), name="assets")
app.mount("/", StaticFiles(directory="static", html=True), name="static")

# 3. Rota de fallback para o Flutter (Deep Linking)
@app.get("/{catchall:path}")
async def catch_all(catchall: str):
    if catchall.startswith("api"):
        return {"detail": "Not Found"}
    # Sempre retorna o index se não for API, para o Flutter gerenciar a rota
    return FileResponse("static/index.html")