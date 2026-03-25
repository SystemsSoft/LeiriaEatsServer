"""Executa as migrations pendentes na BD MySQL."""
from core.database import engine
from sqlalchemy import text

cmds = [
    # localização do estafeta (já aplicado — SKIP se existir)
    "ALTER TABLE drivers ADD COLUMN latitude  DOUBLE NULL",
    "ALTER TABLE drivers ADD COLUMN longitude DOUBLE NULL",
    "ALTER TABLE drivers ADD COLUMN last_seen DATETIME NULL",
    # estafeta atribuído ao pedido (já aplicado — SKIP se existir)
    "ALTER TABLE orders ADD COLUMN driver_id   INT          NULL",
    "ALTER TABLE orders ADD COLUMN driver_name VARCHAR(255) NULL",
    # coordenadas do endereço de entrega
    "ALTER TABLE orders ADD COLUMN delivery_latitude  DOUBLE NULL",
    "ALTER TABLE orders ADD COLUMN delivery_longitude DOUBLE NULL",
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
