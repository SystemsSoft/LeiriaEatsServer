# Arquivo: conectaai/scripts/migrate.py
#
# Aplica as migrações pendentes de conectaai/migrations/*.sql — rodado pelo
# deploy_conectaai.sh antes de reiniciar o serviço. Antes disso as migrações
# eram manuais e, quando alguém esquecia (003_proposal_sender_role), a API
# quebrava em produção com "Unknown column".
#
# Como funciona:
#   1. Base.metadata.create_all — cria as tabelas que ainda não existem (o
#      mesmo que main.py faz no boot), para um ALTER nunca rodar antes da
#      tabela existir num banco novo.
#   2. Cada arquivo .sql, em ordem de nome, que ainda não está em
#      `schema_migrations` é executado comando a comando e, se tudo deu
#      certo, registrado lá. Um erro de verdade para tudo (exit 1) — o deploy
#      aborta antes do restart e o serviço segue com a versão anterior.
#   3. Erros de "já existe" (coluna, índice ou tabela duplicados) contam como
#      sucesso: é o caso de um banco novo, onde create_all já criou a coluna
#      que o ALTER adicionaria, e o dos bancos que já tinham recebido as
#      migrações à mão antes deste script existir.
#
# Regras para escrever uma migração nova:
#   - um arquivo NNN_descricao.sql por mudança; nunca editar um já aplicado;
#   - comandos separados por ";", sem ";" dentro de strings (o separador é
#     simples de propósito);
#   - UMA coluna/índice por comando (um ALTER ... ADD COLUMN por coluna): se
#     um único ALTER adiciona várias e uma já existe, o MySQL recusa o comando
#     inteiro e ele seria tratado como "já existe" sem criar as outras.
#
# Rodar à mão (na raiz do repo do servidor): python -m conectaai.scripts.migrate
import os
import re
import sys
from datetime import datetime
from typing import List, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")

# MySQL: 1050 tabela já existe, 1060 coluna duplicada, 1061 índice duplicado.
_ALREADY_EXISTS_CODES = {1050, 1060, 1061}
_ALREADY_EXISTS_TEXT = ("duplicate column", "already exists", "duplicate key name")


def _statements(sql: str) -> List[str]:
    without_comments = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def _already_exists(error: DBAPIError) -> bool:
    orig = getattr(error, "orig", None)
    code = orig.args[0] if orig is not None and orig.args and isinstance(orig.args[0], int) else None
    return code in _ALREADY_EXISTS_CODES or any(t in str(orig or error).lower() for t in _ALREADY_EXISTS_TEXT)


def _ensure_table(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name VARCHAR(255) NOT NULL PRIMARY KEY,"
            " applied_at DATETIME NOT NULL)"
        ))


def _applied(engine: Engine) -> set:
    with engine.connect() as conn:
        return {row[0] for row in conn.execute(text("SELECT name FROM schema_migrations"))}


def apply_pending(engine: Engine, migrations_dir: str = MIGRATIONS_DIR) -> List[Tuple[str, str]]:
    """Aplica o que falta e devolve [(arquivo, "aplicada" | "já estava no banco")].
    Lança na primeira falha real — o arquivo que falhou não é registrado."""
    _ensure_table(engine)
    applied = _applied(engine)
    results = []
    for name in sorted(f for f in os.listdir(migrations_dir) if re.fullmatch(r"\d+_.+\.sql", f)):
        if name in applied:
            continue
        with open(os.path.join(migrations_dir, name), encoding="utf-8") as fh:
            statements = _statements(fh.read())
        skipped = 0
        for statement in statements:
            try:
                # DDL no MySQL faz commit implícito: cada comando na sua transação.
                with engine.begin() as conn:
                    conn.execute(text(statement))
            except DBAPIError as error:
                if not _already_exists(error):
                    raise RuntimeError(f"Migração {name} falhou em: {statement[:120]}…\n{error}") from error
                skipped += 1
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :at)"),
                {"name": name, "at": datetime.utcnow().replace(microsecond=0)},
            )
        results.append((name, "já estava no banco" if statements and skipped == len(statements) else "aplicada"))
    return results


def main() -> int:
    from conectaai.core.database import Base, engine
    from conectaai.models import sql_models  # noqa: F401 — registra os models na Base

    Base.metadata.create_all(bind=engine)
    try:
        results = apply_pending(engine)
    except RuntimeError as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 1
    if not results:
        print("Migrações: nada pendente.")
    for name, status in results:
        print(f"Migração {name}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
