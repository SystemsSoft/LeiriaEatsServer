
import sys
import os
from sqlalchemy import text

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.database import engine

def migrate():
    with engine.connect() as conn:
        print("🚀 Iniciando migração de relacionamentos para GID...")
        
        # Desabilitar verificações
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        
        steps = [
            # Garantir que as colunas GID são únicas e indexadas
            "ALTER TABLE orders MODIFY COLUMN gid VARCHAR(255) NOT NULL",
            "ALTER TABLE orders ADD UNIQUE (gid)",
            "ALTER TABLE restaurants MODIFY COLUMN gid VARCHAR(255) NOT NULL",
            "ALTER TABLE restaurants ADD UNIQUE (gid)",
            "ALTER TABLE sub_orders MODIFY COLUMN gid VARCHAR(255) NOT NULL",
            "ALTER TABLE sub_orders ADD UNIQUE (gid)",
            
            # Limpar e preparar sub_orders
            "ALTER TABLE sub_orders DROP COLUMN IF EXISTS order_id",
            "ALTER TABLE sub_orders DROP COLUMN IF EXISTS restaurant_id",
            "ALTER TABLE sub_orders ADD COLUMN IF NOT EXISTS master_order_gid VARCHAR(255) NOT NULL",
            "ALTER TABLE sub_orders ADD COLUMN IF NOT EXISTS restaurant_gid VARCHAR(255)",
            
            # Adicionar novas FKs
            "ALTER TABLE sub_orders ADD CONSTRAINT fk_sub_orders_master_gid FOREIGN KEY (master_order_gid) REFERENCES orders(gid) ON DELETE CASCADE",
            "ALTER TABLE sub_orders ADD CONSTRAINT fk_sub_orders_restaurant_gid FOREIGN KEY (restaurant_gid) REFERENCES restaurants(gid) ON DELETE SET NULL",
            
            # order_items
            "ALTER TABLE order_items DROP COLUMN IF EXISTS sub_order_id",
            "ALTER TABLE order_items ADD COLUMN IF NOT EXISTS sub_order_gid VARCHAR(255)",
            "ALTER TABLE order_items ADD CONSTRAINT fk_order_items_sub_order_gid FOREIGN KEY (sub_order_gid) REFERENCES sub_orders(gid) ON DELETE CASCADE"
        ]

        for sql in steps:
            try:
                # Substituir DROP COLUMN IF EXISTS e ADD COLUMN IF NOT EXISTS para compatibilidade básica
                final_sql = sql.replace("IF EXISTS ", "").replace("IF NOT EXISTS ", "")
                conn.execute(text(final_sql))
                conn.commit()
                print(f"✅ OK: {final_sql[:50]}...")
            except Exception as e:
                print(f"⏭️  SKIP/Erro: {str(e)[:100]}")

        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        conn.commit()
        print("\n🎉 Migração finalizada.")

if __name__ == "__main__":
    migrate()
