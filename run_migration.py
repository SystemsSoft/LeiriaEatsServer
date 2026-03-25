"""Executa a migration de localização dos estafetas na BD MySQL."""
from core.database import engine
from sqlalchemy import text

cmds = [
    "ALTER TABLE drivers ADD COLUMN latitude  DOUBLE NULL",
    "ALTER TABLE drivers ADD COLUMN longitude DOUBLE NULL",
    "ALTER TABLE drivers ADD COLUMN last_seen DATETIME NULL",
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

