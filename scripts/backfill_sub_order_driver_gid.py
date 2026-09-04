import sys
import os
from sqlalchemy import text

# Adiciona o diretório raiz ao path para importar os módulos do projeto
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import engine

# Popula sub_orders.driver_gid a partir do antigo sub_orders.driver_id,
# casando com drivers.gid. Rode DEPOIS de backfill_driver_ulids.py
# (senão drivers ainda sem gid ficam de fora do UPDATE).
SQL = """
UPDATE sub_orders so
JOIN drivers d ON d.id = so.driver_id
SET so.driver_gid = d.gid
WHERE so.driver_id IS NOT NULL
  AND (so.driver_gid IS NULL OR so.driver_gid = '')
"""


def backfill():
    with engine.connect() as conn:
        result = conn.execute(text(SQL))
        conn.commit()
        print(f"🎉 {result.rowcount} sub_orders atualizados com driver_gid.")


if __name__ == "__main__":
    backfill()
