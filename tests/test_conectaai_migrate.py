"""
Teste do aplicador de migrações (conectaai/scripts/migrate.py), rodado pelo deploy_conectaai.sh.

Confere: aplica o que falta e registra em schema_migrations; não reaplica; "coluna/índice já existe" conta
como aplicada (banco novo, onde create_all já criou a coluna, ou migração feita à mão antes); um erro de
verdade para tudo sem registrar o arquivo que falhou nem rodar os seguintes.

Roda contra SQLite em memória, sem rede.

Execução (na raiz do repo do servidor):
    python3 tests/test_conectaai_migrate.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from conectaai.scripts.migrate import apply_pending


def _engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE proposals (id VARCHAR(32) PRIMARY KEY, budget FLOAT)"))
    return engine


def _pasta(arquivos):
    pasta = tempfile.mkdtemp()
    for nome, sql in arquivos.items():
        with open(os.path.join(pasta, nome), "w", encoding="utf-8") as fh:
            fh.write(sql)
    return pasta


def _colunas(engine):
    return {c["name"] for c in inspect(engine).get_columns("proposals")}


def _registradas(engine):
    with engine.connect() as c:
        return [r[0] for r in c.execute(text("SELECT name FROM schema_migrations ORDER BY name"))]


def teste_aplica_em_ordem_registra_e_nao_reaplica():
    engine = _engine()
    pasta = _pasta({
        "002_indice.sql": "-- comentário com ; no meio\nCREATE INDEX idx_prop_role ON proposals (sender_role);",
        "001_coluna.sql": "-- adiciona a coluna\nALTER TABLE proposals ADD COLUMN sender_role VARCHAR(20) NOT NULL DEFAULT 'company';",
        "LEIAME.md": "não é migração",
    })
    resultado = apply_pending(engine, pasta)
    assert resultado == [("001_coluna.sql", "aplicada"), ("002_indice.sql", "aplicada")], resultado
    assert "sender_role" in _colunas(engine)
    assert _registradas(engine) == ["001_coluna.sql", "002_indice.sql"]
    assert apply_pending(engine, pasta) == []
    print("OK  - aplica em ordem de nome, registra e não reaplica")


def teste_o_que_ja_existe_conta_como_aplicado():
    engine = _engine()
    with engine.begin() as c:  # banco novo: create_all já criou a coluna
        c.execute(text("ALTER TABLE proposals ADD COLUMN sender_role VARCHAR(20) DEFAULT 'company'"))
    pasta = _pasta({"003_sender_role.sql": "ALTER TABLE proposals ADD COLUMN sender_role VARCHAR(20) NOT NULL DEFAULT 'company';"})
    assert apply_pending(engine, pasta) == [("003_sender_role.sql", "já estava no banco")]
    assert _registradas(engine) == ["003_sender_role.sql"]
    print("OK  - coluna já existente: registra como 'já estava no banco', sem erro")


def teste_erro_de_verdade_para_sem_registrar():
    engine = _engine()
    pasta = _pasta({
        "001_ok.sql": "ALTER TABLE proposals ADD COLUMN a INTEGER;",
        "002_quebrada.sql": "ALTER TABLE tabela_que_nao_existe ADD COLUMN b INTEGER;",
        "003_depois.sql": "ALTER TABLE proposals ADD COLUMN c INTEGER;",
    })
    try:
        apply_pending(engine, pasta)
        raise AssertionError("deveria ter falhado")
    except RuntimeError as erro:
        assert "002_quebrada.sql" in str(erro)
    assert _registradas(engine) == ["001_ok.sql"]
    assert "c" not in _colunas(engine), "migrações depois da que falhou não podem rodar"
    print("OK  - erro real: para na migração quebrada, sem registrá-la nem rodar as seguintes")


if __name__ == "__main__":
    teste_aplica_em_ordem_registra_e_nao_reaplica()
    teste_o_que_ja_existe_conta_como_aplicado()
    teste_erro_de_verdade_para_sem_registrar()
