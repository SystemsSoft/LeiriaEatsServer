
import sys
import os
from sqlalchemy import text

# Adiciona o diretório raiz ao path para importar os módulos do projeto
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import engine

def run_migration():
    cmds = [
        # 1. Criar a tabela sub_orders
        """
        CREATE TABLE IF NOT EXISTS sub_orders (
            id            INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            gid           VARCHAR(255) NULL UNIQUE,
            order_id      INT NOT NULL,
            restaurant_id INT NULL,
            restaurant_name VARCHAR(255) NULL,
            restaurant_category VARCHAR(100) NULL,
            restaurant_image_url VARCHAR(500) NULL,
            status        VARCHAR(50) DEFAULT 'Pendente',
            total         DOUBLE DEFAULT 0,
            delivery_fee  DOUBLE DEFAULT 0,
            base_time     INT DEFAULT 0,
            driver_id     INT NULL,
            driver_name   VARCHAR(255) NULL,
            driver_delivery_fee DOUBLE NULL,
            driver_payment_transfer_id VARCHAR(255) NULL,
            restaurant_latitude  DOUBLE NULL,
            restaurant_longitude DOUBLE NULL,
            CONSTRAINT fk_sub_orders_master FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
            CONSTRAINT fk_sub_orders_restaurant FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE SET NULL,
            CONSTRAINT fk_sub_orders_driver FOREIGN KEY (driver_id) REFERENCES drivers(id) ON DELETE SET NULL
        )
        """,
        # 2. Alterar a tabela orders (Master)
        "ALTER TABLE orders ADD COLUMN gid VARCHAR(255) NULL UNIQUE",
        "ALTER TABLE orders ADD COLUMN total_delivery_fee DOUBLE DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN total_service_fee DOUBLE DEFAULT 0",
    ]

    with engine.connect() as conn:
        # Executar comandos básicos
        for sql in cmds:
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"✅ Executado: {sql.strip()[:50]}...")
            except Exception as e:
                print(f"⏭️  Ignorado/Erro: {e}")

        # 3. Refatorar order_items (Tratamento especial para FK)
        try:
            # Tentar descobrir o nome da constraint
            res = conn.execute(text("SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_NAME = 'order_items' AND COLUMN_NAME = 'order_id' AND REFERENCED_TABLE_NAME = 'orders'"))
            row = res.fetchone()
            if row:
                fk_name = row[0]
                print(f"🔍 Encontrada FK: {fk_name}")
                conn.execute(text(f"ALTER TABLE order_items DROP FOREIGN KEY {fk_name}"))
                conn.commit()
                print(f"✅ Removida FK: {fk_name}")
            
            # Mudar coluna e adicionar nova FK
            conn.execute(text("ALTER TABLE order_items CHANGE COLUMN order_id sub_order_id INT"))
            conn.execute(text("ALTER TABLE order_items ADD CONSTRAINT fk_order_items_sub_order FOREIGN KEY (sub_order_id) REFERENCES sub_orders(id) ON DELETE CASCADE"))
            conn.commit()
            print("✅ order_items atualizada com sucesso.")
        except Exception as e:
            print(f"❌ Erro ao atualizar order_items: {e}")

if __name__ == "__main__":
    run_migration()
