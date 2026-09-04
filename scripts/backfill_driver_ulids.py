import sys
import os

# Adiciona o diretório raiz ao path para importar os módulos do projeto
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import SessionLocal
from core.sql_models import DriverDB
from ulid import ULID


def backfill_ulids():
    db = SessionLocal()
    try:
        drivers = db.query(DriverDB).filter(
            (DriverDB.gid == None) | (DriverDB.gid == "")
        ).all()

        if not drivers:
            print("✨ Todos os estafetas já possuem GID. Nada a fazer.")
            return

        print(f"🚀 Encontrados {len(drivers)} estafetas para atualizar.")

        for driver in drivers:
            new_gid = str(ULID())
            driver.gid = new_gid
            print(f"✅ Atualizando '{driver.login}' (ID: {driver.id}) -> GID: {new_gid}")

        db.commit()
        print("\n🎉 Todos os registros foram atualizados com sucesso!")

    except Exception as e:
        db.rollback()
        print(f"❌ Erro durante a atualização: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    backfill_ulids()
