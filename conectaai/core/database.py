# Arquivo: conectaai/core/database.py
#
# engine/Base/SessionLocal PRÓPRIOS do módulo ConectaAI — isolados dos do Koma
# (core/database.py, na raiz). Nenhuma tabela ou metadado é compartilhado entre
# os dois: Base.metadata.create_all() daqui nunca cria/altera tabelas do KomaDB,
# e vice-versa.
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from conectaai.core.config import settings

SQLALCHEMY_DATABASE_URL = (
    f"mysql+pymysql://{settings.DB_USER}:{settings.DB_PASS}"
    f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=280,
    pool_pre_ping=True,
    connect_args={
        "connect_timeout": 10,
        "read_timeout": 30,
        "write_timeout": 30,
    },
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
