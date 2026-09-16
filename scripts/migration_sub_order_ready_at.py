"""
Migração de sub_orders.ready_at (PLANO_RECOLHA_MULTI_RESTAURANTE.md, Fase 2).

Coluna nullable, sem backfill — pedidos existentes simplesmente não têm sinal real de
prontidão (o worker de despacho continua a usar a estimativa por base_time para eles).

Uso: python3 scripts/migration_sub_order_ready_at.py
"""
import sys
import os
from sqlalchemy import text

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import engine


def add_column():
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE sub_orders ADD COLUMN ready_at DATETIME NULL"))
            conn.commit()
            print("✅ Coluna ready_at criada em sub_orders.")
        except Exception as e:
            print(f"⚠️  Coluna pode já existir, seguindo: {e}")


if __name__ == "__main__":
    add_column()
