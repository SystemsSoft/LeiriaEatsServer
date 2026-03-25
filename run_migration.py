"""Executa as migrations pendentes na BD MySQL."""
from core.database import engine
from sqlalchemy import text

cmds = [
    # 2026-03-25: localização do estafeta
    "ALTER TABLE drivers ADD COLUMN latitude  DOUBLE NULL",
    "ALTER TABLE drivers ADD COLUMN longitude DOUBLE NULL",
    "ALTER TABLE drivers ADD COLUMN last_seen DATETIME NULL",
    # 2026-03-25: estafeta atribuído ao pedido
    "ALTER TABLE orders ADD COLUMN driver_id   INT          NULL",
    "ALTER TABLE orders ADD COLUMN driver_name VARCHAR(255) NULL",
]

with engine.connect() as conn:
    for sql in cmds:
        try:
            conn.execute(text(sql))
            conn.commit()
            print(f"✅ OK: {sql}")
        except Exception as e:
            print(f"⏭️  SKIP (já existe ou erro): {e}")

print("\n🎉 Migration concluída.")

