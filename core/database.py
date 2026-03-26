from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker


DB_USER = "dbmasteruser"
DB_PASS = "q1w2e3r4"
DB_HOST = "ls-4c09769be49b9f8b7ca900b4ecadba80d77c8a07.cq7sywsga5zr.us-east-1.rds.amazonaws.com"
DB_PORT = "3306"
DB_NAME = "LeiriaEatsDB"

# String de Conexão no formato Python (SQLAlchemy)
# Sintaxe: mysql+pymysql://usuario:senha@host:porta/nome_banco
SQLALCHEMY_DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# --- CRIAÇÃO DA ENGINE ---
# pool_recycle é importante para conexões AWS que caem por inatividade
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    # ── Pool de conexões ────────────────────────────────────────────────────
    pool_size=5,               # conexões permanentes no pool
    max_overflow=10,           # conexões extras permitidas acima do pool_size
    pool_timeout=30,           # segundos para aguardar uma conexão livre no pool
    # ── Evitar conexões mortas (AWS NAT encerra idle após ~300s) ───────────
    pool_recycle=280,          # recicla conexões ao fim de 280s (abaixo do limite AWS)
    pool_pre_ping=True,        # testa a conexão antes de usar (SELECT 1)
    # ── Timeout de rede na ligação inicial ─────────────────────────────────
    connect_args={
        "connect_timeout": 10,     # falha rápida se não conseguir ligar em 10s
        "read_timeout":    30,     # timeout para leitura de dados (evita hang indefinido)
        "write_timeout":   30,     # timeout para escrita de dados
    },
)

# --- SESSÃO DO BANCO ---
# Cada requisição vai usar uma instância dessa SessionLocal
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# --- BASE PARA OS MODELS ---
# Todos os seus modelos (tabelas) vão herdar dessa classe
Base = declarative_base()

# Função auxiliar para pegar a conexão nas rotas (Dependency Injection)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()