
import sys
import os
from sqlalchemy import text

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.database import engine

def finish():
    with engine.connect() as conn:
        print("🧹 Limpando dados antigos para aplicar nova estrutura...")
        try:
            # Desabilitar verificações para limpar
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
            conn.execute(text("TRUNCATE TABLE order_items"))
            conn.execute(text("TRUNCATE TABLE orders"))
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
            conn.commit()
            print("✅ Tabelas de pedidos limpas.")
            
            # Tentar aplicar a alteração final de order_items
            # Note: execute_order_refactor.py já renomeou para sub_order_id mas falhou na FK
            try:
                conn.execute(text("ALTER TABLE order_items ADD CONSTRAINT fk_order_items_sub_order FOREIGN KEY (sub_order_id) REFERENCES sub_orders(id) ON DELETE CASCADE"))
                conn.commit()
                print("✅ Constraint de sub_orders aplicada em order_items.")
            except Exception as e:
                if "Duplicate foreign key constraint name" in str(e) or "already exists" in str(e):
                    print("⏭️  Constraint já existe.")
                else:
                    print(f"⚠️  Aviso ao aplicar FK (pode já estar aplicada): {e}")

            print("\n🎉 Migração finalizada com sucesso!")
        except Exception as e:
            print(f"❌ Erro fatal: {e}")

if __name__ == "__main__":
    finish()
