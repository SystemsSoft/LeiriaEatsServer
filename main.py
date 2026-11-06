# Arquivo: main.py
import asyncio
import fcntl

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import FileResponse

from core.database import Base, engine, SessionLocal
from services.ai_service import AIService
from services.hybrid_ai_service import HybridAIService
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
    print("🚀 Iniciando Koma AI Server...")

    # Inicializar modelos de IA (E5 + Gemini)
    print("\n" + "="*50)
    print("🤖 CARREGANDO MODELOS DE INTELIGÊNCIA ARTIFICIAL")
    print("="*50)

    try:
        # Carregar sistema híbrido (E5 local + Gemini API)
        HybridAIService.initialize_models()
        print("✅ Sistema de IA híbrido inicializado com sucesso!")
    except Exception as e:
        print(f"⚠️ Erro ao carregar modelos de IA: {e}")
        print("⚠️ O servidor continuará, mas a IA conversacional pode não funcionar.")

    print("="*50 + "\n")

    # Carregar dados do banco para a IA
    db = SessionLocal()
    try:
        print("📊 Carregando dados do banco para indexação...")
        AIService.reload_data(db)
        print("✅ Dados indexados com sucesso!")
    except Exception as e:
        print(f"⚠️ Erro ao carregar dados: {e}")
    finally:
        db.close()

    asyncio.create_task(_tarefas_de_fundo_num_so_worker())

    print("\n🎉 Servidor pronto para receber requisições!\n")


# Com vários workers do uvicorn cada processo corre o startup, e as tarefas de fundo
# (notificação de estafetas, reconciliação de pagamentos) duplicariam notificações e
# cancelamentos. Fica com elas só o worker que obtiver esta trava exclusiva de ficheiro;
# os outros tentam de novo a cada 30 s e assumem se esse processo morrer (o sistema
# operativo liberta a trava com ele). Serve para vários workers na MESMA máquina.
_FICHEIRO_TRAVA_TAREFAS = os.getenv("TAREFAS_FUNDO_TRAVA", "/tmp/leiria-eats-tarefas-fundo.lock")
_trava_tarefas = None  # o ficheiro tem de ficar aberto: a trava vive enquanto ele estiver


async def _tarefas_de_fundo_num_so_worker():
    global _trava_tarefas
    ficheiro = open(_FICHEIRO_TRAVA_TAREFAS, "w")
    while True:
        try:
            fcntl.flock(ficheiro, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            await asyncio.sleep(30)
    _trava_tarefas = ficheiro
    print(f"🟢 [PID {os.getpid()}] Este worker corre as tarefas de fundo (estafetas e reconciliação)", flush=True)

    asyncio.create_task(courier_notification_worker())
    from services.payment_reconciliation_service import payment_reconciliation_worker
    asyncio.create_task(payment_reconciliation_worker())

app.mount("/assets", StaticFiles(directory="static/assets"), name="assets")
app.mount("/", StaticFiles(directory="static", html=True), name="static")

# 3. Rota de fallback para o Flutter (Deep Linking)
@app.get("/{catchall:path}")
async def catch_all(catchall: str):
    if catchall.startswith("api"):
        return {"detail": "Not Found"}
    # Sempre retorna o index se não for API, para o Flutter gerenciar a rota
    return FileResponse("static/index.html")