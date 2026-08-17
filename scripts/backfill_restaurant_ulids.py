import sys
import os

# Adiciona o diretório raiz ao path para importar os módulos do projeto
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import SessionLocal
from core.sql_models import RestaurantDB
from ulid import ULID

def backfill_ulids():
    db = SessionLocal()
    try:
        # Busca restaurantes que não possuem GID
        restaurants = db.query(RestaurantDB).filter(
            (RestaurantDB.gid == None) | (RestaurantDB.gid == "")
        ).all()
        
        if not restaurants:
            print("✨ Todos os restaurantes já possuem GID. Nada a fazer.")
            return

        print(f"🚀 Encontrados {len(restaurants)} restaurantes para atualizar.")
        
        for restaurant in restaurants:
            new_gid = str(ULID())
            restaurant.gid = new_gid
            print(f"✅ Atualizando '{restaurant.name}' (ID: {restaurant.id}) -> GID: {new_gid}")
        
        db.commit()
        print("\n🎉 Todos os registros foram atualizados com sucesso!")
        
    except Exception as e:
        db.rollback()
        print(f"❌ Erro durante a atualização: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    backfill_ulids()
