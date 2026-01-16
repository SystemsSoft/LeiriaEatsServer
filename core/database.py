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
    pool_recycle=3600,
    pool_pre_ping=True
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