"""
Preenche products.rating (1 a 5, número inteiro — mesmo padrão dos ratings já
existentes no banco: 2.0, 3.0, 4.0, 5.0) para todo produto que ainda está com
rating NULL. Hoje isso cobre os 500 produtos criados por
scripts/seed_50_restaurantes_leiria.py.

Uso: python3 scripts/preencher_ratings_produtos.py [--commit]
Sem --commit roda em modo dry-run.
"""
import argparse
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import SessionLocal
from core.sql_models import ProductDB

random.seed(7)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="Grava de verdade no banco (default: dry-run)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        produtos = db.query(ProductDB).filter(ProductDB.rating.is_(None)).all()
        print(f"Produtos sem rating encontrados: {len(produtos)}")

        for produto in produtos:
            produto.rating = float(random.randint(1, 5))

        if args.commit:
            db.commit()
            print(f"✅ {len(produtos)} produtos atualizados com rating (1 a 5).")
        else:
            db.rollback()
            print(f"🔎 DRY-RUN — {len(produtos)} produtos SERIAM atualizados. Rode com --commit para gravar.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
