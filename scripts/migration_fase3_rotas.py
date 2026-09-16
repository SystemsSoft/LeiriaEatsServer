"""
Migração da Fase 3 — rota agrupada (PLANO_RECOLHA_MULTI_RESTAURANTE.md, secções 5.1, 5.2, 5.6).

- Cria `delivery_routes` e `route_stops` (tabelas novas — Base.metadata.create_all() no
  main.py já faria isso no próximo boot, mas rodar explícito deixa o efeito visível e
  reprodutível antes do deploy, e permite conferir antes de reiniciar o serviço).
- Cria os índices que faltam em `sub_orders` (status, master_order_gid) — sem eles a
  query de despacho filtra por status e junta por master_order_gid em table scan.

Sem backfill: as tabelas novas não têm leitores até o worker de despacho passar a
escrever nelas (feito nesta mesma fase). Idempotente — pode rodar mais de uma vez.

Uso: python3 scripts/migration_fase3_rotas.py
"""
import sys
import os
from sqlalchemy import text

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import engine, Base
import core.sql_models  # garante que todos os modelos estão registados em Base.metadata


def create_tables():
    Base.metadata.create_all(bind=engine, tables=[
        Base.metadata.tables["delivery_routes"],
        Base.metadata.tables["route_stops"],
    ])
    print("✅ Tabelas delivery_routes e route_stops garantidas.")


def create_indexes():
    statements = [
        "CREATE INDEX idx_sub_orders_status ON sub_orders(status)",
        "CREATE INDEX idx_sub_orders_master ON sub_orders(master_order_gid)",
    ]
    with engine.connect() as conn:
        for stmt in statements:
            try:
                conn.execute(text(stmt))
                conn.commit()
                print(f"✅ {stmt}")
            except Exception as e:
                print(f"⚠️  Índice pode já existir, seguindo: {e}")


if __name__ == "__main__":
    create_tables()
    create_indexes()
