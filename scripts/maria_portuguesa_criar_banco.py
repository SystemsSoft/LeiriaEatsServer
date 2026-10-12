"""
Cria o banco de dados e as tabelas da Maria Portuguesa.

É a mesma rotina que o servidor corre ao arrancar (main.py); este script serve para
a correr à mão e ver o resultado. É idempotente: não apaga nem altera nada que já
exista.

Execução (a partir da raiz do servidor):
    python scripts/maria_portuguesa_criar_banco.py          # cria no servidor MySQL
    python scripts/maria_portuguesa_criar_banco.py --sql    # só mostra o SQL, sem ligar

O servidor e o nome do banco vêm de modules/maria_portuguesa/database.py
(variáveis MP_DB_NAME / MP_DATABASE_URL).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _saida in (sys.stdout, sys.stderr):
    _saida.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateIndex, CreateTable

from modules.maria_portuguesa import database, models  # noqa: F401 — regista as tabelas


def sql_de_criacao() -> str:
    """SQL equivalente ao que criar_banco_e_tabelas() executa num servidor MySQL."""
    nome = make_url(database.MP_DATABASE_URL).database
    dialeto = mysql.dialect()
    linhas = [
        f"CREATE DATABASE IF NOT EXISTS `{nome}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
        f"USE `{nome}`;",
    ]
    for tabela in database.Base.metadata.sorted_tables:
        criar = str(CreateTable(tabela, if_not_exists=True).compile(dialect=dialeto)).strip()
        linhas.append(criar + ";")
        for indice in sorted(tabela.indexes, key=lambda i: i.name):
            linhas.append(str(CreateIndex(indice).compile(dialect=dialeto)).strip() + ";")
    return "\n\n".join(linhas) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cria o banco e as tabelas da Maria Portuguesa")
    parser.add_argument("--sql", action="store_true", help="mostra o SQL em vez de o executar")
    args = parser.parse_args()

    if args.sql:
        print(sql_de_criacao())
        sys.exit(0)

    destino = make_url(database.MP_DATABASE_URL)
    print(f"A preparar o banco «{destino.database}» em {destino.host or 'ficheiro local'}...")
    try:
        tabelas = database.criar_banco_e_tabelas()
    except Exception as e:
        print(f"❌ Não foi possível criar o banco: {e}")
        sys.exit(1)

    inspetor = inspect(database.engine)
    for tabela in tabelas:
        colunas = [coluna["name"] for coluna in inspetor.get_columns(tabela)]
        print(f"✅ {tabela} ({len(colunas)} colunas): {', '.join(colunas)}")
