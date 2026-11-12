"""
Converte para PBKDF2 as senhas de restaurantes ainda guardadas em texto simples
(core/senhas.py). As que já estão em PBKDF2 ficam como estão. Idempotente.

Uso (no servidor):
    python scripts/hash_senhas_restaurantes.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from core.database import SessionLocal
from core.senhas import gerar_hash, precisa_rehash
from core.sql_models import RestaurantDB


def main():
    with SessionLocal() as db:
        restaurantes = db.query(RestaurantDB).all()
        convertidos = 0
        for r in restaurantes:
            if r.password and precisa_rehash(r.password):
                r.password = gerar_hash(r.password)
                convertidos += 1
        db.commit()
    print(f"{len(restaurantes)} restaurantes, {convertidos} senhas convertidas para PBKDF2.")


if __name__ == "__main__":
    main()
