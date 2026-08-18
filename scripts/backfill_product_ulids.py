import sys
import os

# Adiciona o diretório raiz ao path para importar os módulos do projeto
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import SessionLocal
from core.sql_models import ProductDB
from ulid import ULID

def backfill_ulids():
    db = SessionLocal()
    try:
        # Busca produtos que não possuem GID
        products = db.query(ProductDB).filter(
            (ProductDB.gid == None) | (ProductDB.gid == "")
        ).all()
        
        if not products:
            print("✨ Todos os produtos já possuem GID. Nada a fazer.")
            return

        print(f"🚀 Encontrados {len(products)} produtos para atualizar.")
        
        for product in products:
            new_gid = str(ULID())
            product.gid = new_gid
            print(f"✅ Atualizando '{product.name}' (ID: {product.id}) -> GID: {new_gid}")
        
        db.commit()
        print("\n🎉 Todos os registros foram atualizados com sucesso!")
        
    except Exception as e:
        db.rollback()
        print(f"❌ Erro durante a atualização: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    backfill_ulids()
