"""
Migração + backfill de products.transit_tolerance_minutes
(PLANO_RECOLHA_MULTI_RESTAURANTE.md, secção 4.4).

Sequência (a ordem importa — ver o plano):
1. ADD COLUMN nullable — NOT NULL de imediato falharia nas linhas já existentes.
2. Backfill de todo produto com o valor NULO, usando o fallback por categoria
   (services/transit_tolerance_service), a mesma função que o resto do backend usa em
   tempo de leitura — garante que backfill e fallback nunca divergem.

NÃO torna a coluna NOT NULL no banco — decisão deliberada (ver 5.5 do plano): o fallback
em tempo de leitura já cobre valores nulos, e a obrigatoriedade real vive no formulário do
KomaRestaurant e na validação do schema de escrita.

Uso: python3 scripts/migration_transit_tolerance.py [--commit]
Sem --commit roda em modo dry-run (mostra o que seria feito, não grava nada).
"""
import argparse
import sys
import os
from sqlalchemy import text

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import engine, SessionLocal
from core.sql_models import ProductDB
from services.transit_tolerance_service import tolerancia_padrao_por_categoria


def add_column():
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE products ADD COLUMN transit_tolerance_minutes INT NULL"))
            conn.commit()
            print("✅ Coluna transit_tolerance_minutes criada.")
        except Exception as e:
            print(f"⚠️  Coluna pode já existir, seguindo: {e}")


def backfill(commit: bool):
    db = SessionLocal()
    try:
        produtos = db.query(ProductDB).filter(ProductDB.transit_tolerance_minutes.is_(None)).all()
        print(f"Produtos sem tolerância de trânsito: {len(produtos)}")

        por_categoria: dict[str, int] = {}
        for produto in produtos:
            valor = tolerancia_padrao_por_categoria(produto.category)
            produto.transit_tolerance_minutes = valor
            por_categoria[produto.category or "(sem categoria)"] = valor

        if commit:
            db.commit()
            print(f"✅ {len(produtos)} produtos atualizados via backfill.")
        else:
            db.rollback()
            print(f"🔎 DRY-RUN — {len(produtos)} produtos SERIAM atualizados. Rode com --commit para gravar.")

        print("\nAmostra categoria -> tolerância aplicada:")
        for categoria, valor in sorted(por_categoria.items(), key=lambda kv: kv[0].lower()):
            print(f"   {categoria:34} -> {valor} min")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="Grava de verdade no banco (default: dry-run)")
    args = parser.parse_args()

    # A criação da coluna sempre roda (é aditiva e nullable — mesmo padrão dos outros
    # scripts de migração do projeto). O --commit só afeta o BACKFILL de dados abaixo.
    add_column()
    backfill(commit=args.commit)
