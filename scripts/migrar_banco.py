"""
Copia a base de dados do Koma (KomaDB) do servidor MySQL antigo para o novo, com
mysqldump: estrutura, dados, índices e chaves estrangeiras. Usa o mesmo
utilizador, senha e porta de core/database.py.

Por segurança, se a base no destino já tiver tabelas o script pára — use
--substituir para apagar e recriar (por exemplo na sincronização final, logo
antes de mudar o servidor para o banco novo). No fim compara o número exato de
linhas de cada tabela entre origem e destino.

Precisa do cliente MySQL (brew install mysql-client).

Uso:
    python scripts/migrar_banco.py --origem HOST_ANTIGO --destino HOST_NOVO
    python scripts/migrar_banco.py --origem HOST_ANTIGO --destino HOST_NOVO --substituir
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql

from core.database import DB_NAME, DB_PASS, DB_PORT, DB_USER

PASTAS_CLIENTE = ["/opt/homebrew/opt/mysql-client/bin", "/usr/local/opt/mysql-client/bin"]


def _binario(nome: str) -> str:
    for pasta in PASTAS_CLIENTE:
        caminho = os.path.join(pasta, nome)
        if os.path.exists(caminho):
            return caminho
    caminho = shutil.which(nome)
    if not caminho:
        sys.exit(f"{nome} não encontrado — instale com: brew install mysql-client")
    return caminho


def _ligar(host: str, base: str = None):
    return pymysql.connect(host=host, port=int(DB_PORT), user=DB_USER, password=DB_PASS,
                           database=base, connect_timeout=15)


def _contagens(host: str) -> dict:
    with _ligar(host, DB_NAME) as conexao, conexao.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_type = 'BASE TABLE'", (DB_NAME,))
        tabelas = [linha[0] for linha in cur.fetchall()]
        contagens = {}
        for tabela in tabelas:
            cur.execute(f"SELECT COUNT(*) FROM `{tabela}`")
            contagens[tabela] = cur.fetchone()[0]
        return contagens


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--origem", required=True, help="host do MySQL antigo")
    parser.add_argument("--destino", required=True, help="host do MySQL novo")
    parser.add_argument("--substituir", action="store_true", help="apaga a base no destino antes de copiar")
    args = parser.parse_args()

    if args.origem == args.destino:
        sys.exit("Origem e destino são o mesmo servidor.")

    with _ligar(args.destino) as conexao, conexao.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s", (DB_NAME,))
        existentes = cur.fetchone()[0]
        if existentes and not args.substituir:
            sys.exit(f"O destino já tem {existentes} tabelas em {DB_NAME}. Nada foi alterado. "
                     f"Use --substituir para apagar e copiar de novo.")
        if args.substituir:
            cur.execute(f"DROP DATABASE IF EXISTS `{DB_NAME}`")
        cur.execute(f"CREATE DATABASE `{DB_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")

    # Credenciais num ficheiro temporário (0600) em vez da linha de comandos, onde ficariam visíveis no `ps`.
    with tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False) as cnf:
        cnf.write(f"[client]\nuser={DB_USER}\npassword={DB_PASS}\nport={DB_PORT}\n")
    os.chmod(cnf.name, 0o600)
    try:
        print(f"A copiar {DB_NAME}: {args.origem} → {args.destino}")
        dump = subprocess.Popen(
            [_binario("mysqldump"), f"--defaults-extra-file={cnf.name}", f"--host={args.origem}",
             "--single-transaction", "--set-gtid-purged=OFF", "--no-tablespaces",
             "--routines", "--triggers", "--events", "--default-character-set=utf8mb4", DB_NAME],
            stdout=subprocess.PIPE,
        )
        restauro = subprocess.run(
            [_binario("mysql"), f"--defaults-extra-file={cnf.name}", f"--host={args.destino}",
             "--default-character-set=utf8mb4", DB_NAME],
            stdin=dump.stdout,
        )
        dump.stdout.close()
        if dump.wait() != 0 or restauro.returncode != 0:
            sys.exit("A cópia falhou — veja os erros acima.")
    finally:
        os.remove(cnf.name)

    origem, destino = _contagens(args.origem), _contagens(args.destino)
    diferencas = 0
    for tabela in sorted(origem.keys() | destino.keys()):
        a, b = origem.get(tabela), destino.get(tabela)
        marca = "OK " if a == b else "DIF"
        diferencas += a != b
        print(f"  {marca} {tabela}: origem={a} destino={b}")
    if diferencas:
        sys.exit(f"{diferencas} tabelas diferentes entre origem e destino.")
    print(f"Migração concluída: {len(origem)} tabelas, {sum(origem.values())} linhas, tudo igual.")


if __name__ == "__main__":
    main()
