
import sys
import os
from sqlalchemy import text

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.database import engine

def cleanup():
    with engine.connect() as conn:
        print("🚀 Iniciando limpeza forçada de tabelas de pedidos...")
        
        # 1. Desabilitar chaves estrangeiras temporariamente
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        
        # 2. Limpar dados para evitar erros de integridade durante alteração
        conn.execute(text("TRUNCATE TABLE order_items"))
        conn.execute(text("TRUNCATE TABLE sub_orders"))
        conn.execute(text("TRUNCATE TABLE orders"))
        conn.commit()
        print("✅ Dados temporários limpos.")

        # 3. Remover chaves estrangeiras antigas por nome (baseado no erro)
        # Vamos tentar remover as conhecidas e as padrões do MySQL (ibfk_X)
        fk_to_drop = [
            ("sub_orders", "sub_orders_ibfk_1"),
            ("sub_orders", "sub_orders_ibfk_2"),
            ("sub_orders", "fk_sub_orders_master"),
            ("sub_orders", "fk_sub_orders_restaurant"),
        ]
        
        for table, fk in fk_to_drop:
            try:
                conn.execute(text(f"ALTER TABLE {table} DROP FOREIGN KEY {fk}"))
                conn.commit()
                print(f"✅ Removida FK: {fk} da tabela {table}")
            except Exception as e:
                print(f"⏭️  FK {fk} não encontrada ou já removida.")

        # 4. Remover colunas INT antigas que não são mais usadas
        cols_to_drop = [
            ("sub_orders", "order_id"),
            ("sub_orders", "restaurant_id"),
        ]
        
        for table, col in cols_to_drop:
            try:
                conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {col}"))
                conn.commit()
                print(f"✅ Removida coluna: {col} da tabela {table}")
            except Exception as e:
                print(f"⏭️  Coluna {col} não encontrada.")

        # 5. Garantir que as novas FKs baseadas em GID estão lá
        # Primeiro garantir que gid em orders e restaurants é UNIQUE
        try:
            conn.execute(text("ALTER TABLE orders ADD UNIQUE (gid)"))
            conn.commit()
        except: pass
        
        try:
            conn.execute(text("ALTER TABLE restaurants ADD UNIQUE (gid)"))
            conn.commit()
        except: pass

        try:
            conn.execute(text("ALTER TABLE sub_orders ADD CONSTRAINT fk_sub_orders_master_gid FOREIGN KEY (master_order_gid) REFERENCES orders(gid) ON DELETE CASCADE"))
            conn.commit()
            print("✅ FK master_order_gid criada.")
        except Exception as e:
            print(f"⏭️  FK master_order_gid já existe ou erro: {e}")

        try:
            conn.execute(text("ALTER TABLE sub_orders ADD CONSTRAINT fk_sub_orders_restaurant_gid FOREIGN KEY (restaurant_gid) REFERENCES restaurants(gid) ON DELETE SET NULL"))
            conn.commit()
            print("✅ FK restaurant_gid criada.")
        except Exception as e:
            print(f"⏭️  FK restaurant_gid já existe ou erro: {e}")

        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        conn.commit()
        print("\n🎉 Limpeza concluída com sucesso!")

if __name__ == "__main__":
    cleanup()
