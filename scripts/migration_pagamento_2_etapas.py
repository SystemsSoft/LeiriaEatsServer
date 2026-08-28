
import sys
import os
from sqlalchemy import text

# Adiciona o diretório raiz ao path para importar os módulos do projeto
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import engine

def run_migration():
    cmds = [
        "ALTER TABLE orders ADD COLUMN payment_flow VARCHAR(20) NULL DEFAULT 'AUTO_CAPTURE'",
        "ALTER TABLE orders ADD COLUMN payment_status VARCHAR(50) NULL",
        "ALTER TABLE orders ADD COLUMN authorized_amount DOUBLE NULL",
        "ALTER TABLE orders ADD COLUMN captured_amount DOUBLE NULL",
        "ALTER TABLE orders ADD COLUMN authorization_expires_at DATETIME NULL",
        "UPDATE orders SET payment_flow = 'AUTO_CAPTURE' WHERE payment_flow IS NULL",
        "ALTER TABLE sub_orders ADD COLUMN accepted_at DATETIME NULL",
        "ALTER TABLE sub_orders ADD COLUMN declined_at DATETIME NULL",
        "ALTER TABLE sub_orders ADD COLUMN decline_reason VARCHAR(255) NULL",
        "ALTER TABLE sub_orders ADD COLUMN stripe_transfer_id VARCHAR(255) NULL",
        "ALTER TABLE sub_orders ADD COLUMN stripe_transfer_amount DOUBLE NULL",
        "ALTER TABLE sub_orders ADD COLUMN stripe_transfer_reversed DOUBLE NULL DEFAULT 0",
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
