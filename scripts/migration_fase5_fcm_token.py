"""
Migração de drivers.fcm_token (PLANO_RECOLHA_MULTI_RESTAURANTE.md, Fase 5).

Coluna nullable, sem backfill — estafetas existentes simplesmente não têm token até o
app registar um (POST /drivers/{driver_id}/fcm-token). Até lá, o despacho continua
funcionando 100% por polling.

Uso: python3 scripts/migration_fase5_fcm_token.py
"""
import sys
import os
from sqlalchemy import text

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import engine


def add_column():
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE drivers ADD COLUMN fcm_token VARCHAR(500) NULL"))
            conn.commit()
            print("✅ Coluna fcm_token criada em drivers.")
        except Exception as e:
            print(f"⚠️  Coluna pode já existir, seguindo: {e}")


if __name__ == "__main__":
    add_column()
