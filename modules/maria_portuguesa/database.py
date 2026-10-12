# Arquivo: modules/maria_portuguesa/database.py
#
# Banco de dados próprio da Maria Portuguesa. Fica no mesmo servidor MySQL do
# Leiria Eats (mesmas credenciais de core/database.py), mas num banco separado —
# nada do módulo toca no KomaDB.
#
# Variáveis de ambiente (opcionais):
#   MP_DB_NAME        nome do banco no servidor MySQL do Leiria Eats
#                     (por omissão: MariaPortuguesaDB)
#   MP_DATABASE_URL   URL SQLAlchemy completo, para usar outro servidor
#                     (ex.: mysql+pymysql://user:senha@host:3306/MariaPortuguesaDB)
import os
import re

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

from core.database import DB_HOST, DB_PASS, DB_PORT, DB_USER

MP_DB_NAME = os.getenv("MP_DB_NAME", "MariaPortuguesaDB")

MP_DATABASE_URL = os.getenv("MP_DATABASE_URL") or (
    f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{MP_DB_NAME}?charset=utf8mb4"
)

_url = make_url(MP_DATABASE_URL)
_E_MYSQL = _url.get_backend_name() == "mysql"

# Mesmos cuidados de core/database.py com ligações que a AWS deixa cair; o pool é
# mais pequeno porque partilha o limite de ligações do servidor com o Leiria Eats.
engine = create_engine(
    _url,
    pool_size=3,
    max_overflow=5,
    pool_timeout=30,
    pool_recycle=280,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 10, "read_timeout": 30, "write_timeout": 30} if _E_MYSQL else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base própria: o create_all do Leiria Eats (main.py) não cria estas tabelas no
# KomaDB, e o create_all daqui não cria as do Leiria Eats neste banco.
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def criar_banco_e_tabelas() -> list[str]:
    """Cria o banco (se for MySQL e ainda não existir) e as tabelas em falta.

    É idempotente: pode correr a cada arranque do servidor. Não altera tabelas que
    já existam — mudanças de colunas continuam a precisar de uma migração.
    Devolve os nomes das tabelas existentes no fim.
    """
    # Garante que os modelos estão registados na Base antes do create_all.
    from modules.maria_portuguesa import models  # noqa: F401

    if _E_MYSQL:
        nome = _url.database
        # O nome entra na instrução por interpolação (CREATE DATABASE não aceita
        # parâmetros), por isso só se aceitam identificadores simples.
        if not nome or not re.fullmatch(r"[A-Za-z0-9_]+", nome):
            raise ValueError(f"Nome de banco inválido para a Maria Portuguesa: {nome!r}")
        # _replace e não set(): o set() ignora None e deixaria a ligação apontada
        # para o banco que ainda não existe.
        servidor = create_engine(_url._replace(database=None), connect_args={"connect_timeout": 10})
        try:
            with servidor.connect() as ligacao:
                ligacao.execute(text(
                    f"CREATE DATABASE IF NOT EXISTS `{nome}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                ))
                ligacao.commit()
        finally:
            servidor.dispose()

    Base.metadata.create_all(bind=engine)
    return sorted(inspect(engine).get_table_names())
