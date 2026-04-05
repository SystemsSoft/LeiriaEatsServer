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
    # coordenadas do restaurante (copiadas na criação do pedido)
    "ALTER TABLE orders ADD COLUMN restaurant_latitude  DOUBLE NULL",
    "ALTER TABLE orders ADD COLUMN restaurant_longitude DOUBLE NULL",
    # taxas de entrega e de serviço
    "ALTER TABLE orders ADD COLUMN delivery_fee DOUBLE NOT NULL DEFAULT 0",
    "ALTER TABLE orders ADD COLUMN service_fee  DOUBLE NOT NULL DEFAULT 0",
    # estafeta próprio do restaurante (renomear coluna antiga se existir, criar nova)
    "ALTER TABLE restaurants CHANGE COLUMN uses_own_courier use_own_delivery BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE restaurants ADD COLUMN use_own_delivery BOOLEAN NOT NULL DEFAULT FALSE",
    # rating dos produtos pode ser NULL
    "ALTER TABLE products MODIFY COLUMN rating DOUBLE NULL DEFAULT NULL",
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
