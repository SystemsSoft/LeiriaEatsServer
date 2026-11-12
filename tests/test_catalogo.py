"""
Testes do catálogo paginado (api/routes/catalogo_routes.py) com SQLite em memória.

Execução:
    python3 tests/test_catalogo.py
"""
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import api.routes.catalogo_routes as catalogo
from core.database import Base, get_db
from core.sql_models import ProductDB, RestaurantDB

motor = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(bind=motor)
Sessao = sessionmaker(bind=motor)

CATEGORIAS = ["Japonesa, Sushi", "Pizzaria", "Fast Food", "pizzaria, Italiana", "Mexicana"]
with Sessao() as db:
    for i in range(1, 46):  # 45 restaurantes, 3 produtos cada
        r = RestaurantDB(name=f"Restaurante {i}", category=CATEGORIAS[i % len(CATEGORIAS)], login=f"r{i}@x.pt",
                         password="x", gid=f"R{i}", plan="SMART" if i % 10 == 0 else "ESSENCE",
                         image_url=f"https://leiria-eats-bucket.s3.eu-south-2.amazonaws.com/Restaurants/r{i}.webp")
        db.add(r)
        db.flush()
        for j in range(3):
            db.add(ProductDB(name=f"Produto {i}-{j}", description="", price=5.0, restaurant_id=r.id, gid=f"P{i}-{j}",
                             category=["Sushi", "Pizza", "Bebidas"][j]))
    # Produto órfão (restaurante apagado): existe em produção e não pode partir a lista.
    db.add(ProductDB(name="Órfão", description="", price=1.0, restaurant_id=9999, gid="PX", category="Órfãos"))
    db.commit()


def _db():
    with Sessao() as db:
        yield db


app = FastAPI()
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.include_router(catalogo.router)
app.dependency_overrides[get_db] = _db
cliente = TestClient(app)


def _todas_as_paginas(url, **params):
    vistos, cursor, paginas = [], None, 0
    while True:
        catalogo.limpar_cache_do_catalogo()
        r = cliente.get(url, params={**params, **({"cursor": cursor} if cursor else {})})
        assert r.status_code == 200, r.text
        corpo = r.json()
        vistos += corpo["items"]
        paginas += 1
        cursor = corpo["next_cursor"]
        if not cursor:
            return vistos, paginas, corpo["total"]


def teste_restaurantes_em_paginas_sem_repetir_nem_saltar():
    itens, paginas, total = _todas_as_paginas("/restaurants", limit=20)
    assert paginas == 3 and total == 45 and len(itens) == 45, (paginas, total, len(itens))
    assert [r["gid"] for r in itens] == [f"R{i}" for i in range(1, 46)]
    assert "products" not in itens[0] and "login" not in itens[0] and "phone" not in itens[0], itens[0].keys()
    print("OK  - 45 restaurantes em 3 páginas de 20, sem repetir nem saltar, sem cardápio nem login")


def teste_filtro_por_categoria_e_plano():
    itens, _, total = _todas_as_paginas("/restaurants", limit=5, category="pizzaria")
    esperados = [f"R{i}" for i in range(1, 46) if "pizzaria" in CATEGORIAS[i % 5].lower()]
    assert [r["gid"] for r in itens] == esperados and total == len(esperados), (total, len(esperados))
    itens, _, total = _todas_as_paginas("/restaurants", category="Sushi")
    assert total == 9 and all("Sushi" in r["category"] for r in itens)
    itens, _, total = _todas_as_paginas("/restaurants", plan="smart")
    assert [r["gid"] for r in itens] == ["R10", "R20", "R30", "R40"] and total == 4
    print("OK  - categoria (entre várias, sem distinguir maiúsculas) e plano SMART filtram e contam certo")


def teste_produtos_paginados_com_restaurante():
    itens, paginas, total = _todas_as_paginas("/products", limit=50)
    assert total == 135 and len(itens) == 135 and paginas == 3
    p = itens[0]
    assert p["restaurant_name"] == "Restaurante 1" and p["restaurant"]["gid"] == "R1" and p["restaurant_gid"] == "R1", p
    itens, _, total = _todas_as_paginas("/products", category="Bebidas", limit=7)
    assert total == 45 and len(itens) == 45 and all(p["category"] == "Bebidas" for p in itens)
    print("OK  - produtos paginados (135 em 3 páginas), cada um com o resumo do restaurante; filtro por categoria")


def teste_categorias():
    catalogo.limpar_cache_do_catalogo()
    c = cliente.get("/restaurants/categories").json()
    assert c["restaurants"] == ["Fast Food", "Italiana", "Japonesa", "Mexicana", "Pizzaria", "Sushi"], c
    assert c["products"] == ["Bebidas", "Pizza", "Sushi"], c
    print("OK  - categorias distintas e ordenadas (\"Pizzaria\" e \"pizzaria\" contam uma vez)")


def teste_etag_304_e_gzip():
    catalogo.limpar_cache_do_catalogo()
    r = cliente.get("/restaurants", params={"limit": 50}, headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip" and r.headers["cache-control"] == "no-cache"
    etag = r.headers["etag"]
    r2 = cliente.get("/restaurants", params={"limit": 50}, headers={"If-None-Match": etag})
    assert r2.status_code == 304 and r2.content == b"", r2.status_code
    r3 = cliente.get("/restaurants", params={"limit": 10}, headers={"If-None-Match": etag})
    assert r3.status_code == 200
    print("OK  - resposta comprimida em gzip; o mesmo ETag devolve 304 sem corpo")


def teste_cursor_e_limite_invalidos():
    assert cliente.get("/restaurants", params={"cursor": "abc"}).status_code == 400
    assert cliente.get("/restaurants", params={"limit": 500}).status_code == 422
    print("OK  - cursor inválido dá 400 e limite acima de 50 dá 422")


if __name__ == "__main__":
    teste_restaurantes_em_paginas_sem_repetir_nem_saltar()
    teste_filtro_por_categoria_e_plano()
    teste_produtos_paginados_com_restaurante()
    teste_categorias()
    teste_etag_304_e_gzip()
    teste_cursor_e_limite_invalidos()
    print("\nTodos os testes do catálogo passaram.")
