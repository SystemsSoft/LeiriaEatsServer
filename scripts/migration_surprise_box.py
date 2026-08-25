
import sys
import os
from sqlalchemy import text

# Adiciona o diretório raiz ao path para importar os módulos do projeto
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import engine

def run_migration():
    cmds = [
        "ALTER TABLE restaurants ADD COLUMN has_surprise_box BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE restaurants ADD COLUMN surprise_box_qty INT NOT NULL DEFAULT 0",
    ]

    with engine.connect() as conn:
        for sql in cmds:
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"✅ Executado com sucesso: {sql}")
            except Exception as e:
                print(f"⚠️ Erro ao executar (pode já existir): {e}")

if __name__ == "__main__":
    run_migration()
