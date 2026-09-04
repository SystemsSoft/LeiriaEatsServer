import sys
import os
from sqlalchemy import text

# Adiciona o diretório raiz ao path para importar os módulos do projeto
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.database import engine

# Passo aditivo apenas: cria as colunas novas (nullable) e, ao final, a FK nova.
# NÃO remove driver_id nem sua FK antiga — isso é uma etapa destrutiva separada,
# ver o passo 4 (comentado) em migration_add_driver_gid.sql, a rodar manualmente
# só depois de validar em produção.
STEPS = [
    "ALTER TABLE drivers ADD COLUMN gid VARCHAR(255) NULL",
    "ALTER TABLE sub_orders ADD COLUMN driver_gid VARCHAR(255) NULL",
]

# Executados só depois do backfill (rode backfill_driver_ulids.py e
# backfill_sub_order_driver_gid.py antes de rodar este script com --finalize).
FINALIZE_STEPS = [
    "ALTER TABLE drivers ADD CONSTRAINT uq_driver_gid UNIQUE (gid)",
    "ALTER TABLE sub_orders ADD CONSTRAINT fk_sub_orders_driver_gid FOREIGN KEY (driver_gid) REFERENCES drivers(gid) ON DELETE SET NULL",
]


def _run(steps):
    with engine.connect() as conn:
        for sql in steps:
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"✅ OK: {sql[:80]}...")
            except Exception as e:
                print(f"⏭️  SKIP/Erro: {str(e)[:150]}")


def migrate():
    print("🚀 Adicionando colunas gid (drivers) e driver_gid (sub_orders)...")
    _run(STEPS)
    print("🎉 Colunas criadas. Rode agora os scripts de backfill antes de --finalize.")


if __name__ == "__main__":
    if "--finalize" in sys.argv:
        print("🚀 Aplicando UNIQUE e FOREIGN KEY (rode isso só após o backfill)...")
        _run(FINALIZE_STEPS)
        print("🎉 Migração finalizada.")
    else:
        migrate()
