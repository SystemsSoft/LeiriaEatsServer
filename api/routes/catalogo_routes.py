# Arquivo: api/routes/catalogo_routes.py
"""Catálogo da app de pedidos, paginado.

Antes, GET /restaurants devolvia TODOS os restaurantes com o cardápio inteiro de cada um dentro
(~650 bytes por produto): com centenas de restaurantes seriam vários MB a cada abertura da app.
Agora o catálogo vem em páginas e sem cardápios — o cardápio de um restaurante só é pedido
quando o cliente o abre (GET /companies/{gid}):

    GET /restaurants             restaurantes (resumo), filtráveis por categoria e plano
    GET /restaurants/categories  categorias de restaurantes e de produtos, para os filtros
    GET /products                produtos de todos os restaurantes, filtráveis por categoria

As listas usam paginação por cursor: `next_cursor` é o id do último item devolvido e a página
seguinte pede `?cursor=<esse id>`. Ao contrário de "página 2, 3…", um restaurante novo não faz
repetir nem saltar itens, e continua rápido com milhares de linhas (usa o índice do id).

Todas as respostas levam ETag (core/cache_http.py) e ficam guardadas 5 s no processo: com
muitos clientes ao mesmo tempo, um pedido calcula e os outros reutilizam.
"""
import threading
import time
from typing import Callable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, contains_eager

from core.cache_http import resposta_com_etag
from core.database import get_db
from core.sql_models import ProductDB, RestaurantDB
from schemas.models import Product

router = APIRouter()

LIMITE_PADRAO = 20
LIMITE_MAXIMO = 50
_TTL = 5.0
_MAX_ENTRADAS = 512


class RestauranteResumo(BaseModel):
    """O que as listas da app mostram de um restaurante (sem cardápio, login nem contactos)."""
    id: int
    gid: Optional[str] = ""
    name: str
    category: str
    image_url: Optional[str] = None
    rating: Optional[float] = None
    plan: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    has_surprise_box: bool = False

    class Config:
        from_attributes = True


class PaginaRestaurantes(BaseModel):
    items: List[RestauranteResumo]
    next_cursor: Optional[str] = None
    total: int


class ProdutoDoCatalogo(Product):
    restaurant_name: Optional[str] = None
    restaurant_plan: Optional[str] = None
    restaurant: RestauranteResumo


class PaginaProdutos(BaseModel):
    items: List[ProdutoDoCatalogo]
    next_cursor: Optional[str] = None
    total: int


class Categorias(BaseModel):
    restaurants: List[str]
    products: List[str]


# ── cache curto por URL ──────────────────────────────────────────────────────────────────────
_cache: dict = {}
_trava = threading.Lock()


def _em_cache(chave: tuple, calcular: Callable):
    agora = time.monotonic()
    entrada = _cache.get(chave)
    if entrada and agora - entrada[0] < _TTL:
        return entrada[1]
    with _trava:
        entrada = _cache.get(chave)  # quem esperou pela trava reutiliza o que acabou de ser calculado
        if entrada and time.monotonic() - entrada[0] < _TTL:
            return entrada[1]
        dados = calcular()
        if len(_cache) >= _MAX_ENTRADAS:
            _cache.clear()
        _cache[chave] = (time.monotonic(), dados)
        return dados


def limpar_cache_do_catalogo():
    _cache.clear()


# ── auxiliares ───────────────────────────────────────────────────────────────────────────────
def _partes(categorias: Optional[str]) -> List[str]:
    """"Japonesa, Sushi" -> ["Japonesa", "Sushi"] (a coluna guarda várias separadas por vírgula)."""
    return [c.strip() for c in (categorias or "").split(",") if c.strip()]


def _tem_categoria(categorias: Optional[str], procurada: str) -> bool:
    procurada = procurada.strip().lower()
    return any(c.lower() == procurada for c in _partes(categorias))


def _ler_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    try:
        return int(cursor)
    except ValueError:
        raise HTTPException(status_code=400, detail="cursor inválido")


def _paginar(consulta, modelo, depois_de: int, limite: int, aceita: Callable):
    """Até `limite` linhas com id > depois_de que passem em `aceita`, mais o cursor seguinte.

    A categoria é filtrada em SQL só de forma aproximada (LIKE) e confirmada aqui, por isso
    lê em lotes até encher a página."""
    itens, ultimo, lote = [], depois_de, max(limite * 2, 20)
    while len(itens) <= limite:
        linhas = consulta.filter(modelo.id > ultimo).order_by(modelo.id).limit(lote).all()
        for linha in linhas:
            ultimo = linha.id
            if aceita(linha):
                itens.append(linha)
                if len(itens) > limite:
                    break
        if len(linhas) < lote:
            break
    tem_mais = len(itens) > limite
    itens = itens[:limite]
    return itens, (str(itens[-1].id) if tem_mais and itens else None)


def _filtro_aproximado(coluna, categoria: str):
    return func.lower(coluna).contains(categoria.strip().lower(), autoescape=True)


# ── rotas ────────────────────────────────────────────────────────────────────────────────────
@router.get("/restaurants", response_model=PaginaRestaurantes)
def listar_restaurantes(
    request: Request,
    limit: int = Query(LIMITE_PADRAO, ge=1, le=LIMITE_MAXIMO),
    cursor: Optional[str] = None,
    category: Optional[str] = None,
    plan: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Restaurantes por ordem de registo, `limit` de cada vez. `category` filtra por uma das
    categorias do restaurante; `plan` por plano (ex.: SMART para os destaques)."""
    depois_de = _ler_cursor(cursor)
    categoria = (category or "").strip() or None
    plano = (plan or "").strip().upper() or None

    def calcular():
        consulta = db.query(RestaurantDB)
        if plano:
            consulta = consulta.filter(func.upper(RestaurantDB.plan) == plano)
        if categoria:
            consulta = consulta.filter(_filtro_aproximado(RestaurantDB.category, categoria))
        aceita = (lambda r: _tem_categoria(r.category, categoria)) if categoria else (lambda r: True)
        itens, proximo = _paginar(consulta, RestaurantDB, depois_de, limit, aceita)
        if categoria:
            total = sum(1 for (c,) in consulta.with_entities(RestaurantDB.category) if _tem_categoria(c, categoria))
        else:
            total = consulta.count()
        return PaginaRestaurantes(
            items=[RestauranteResumo.model_validate(r) for r in itens], next_cursor=proximo, total=total
        )

    return resposta_com_etag(request, _em_cache(("restaurants", limit, depois_de, categoria, plano), calcular))


@router.get("/restaurants/categories", response_model=Categorias)
def listar_categorias(request: Request, db: Session = Depends(get_db)):
    """Categorias distintas (ordenadas) de restaurantes e de produtos, para os filtros do Explorar."""

    def distintas(coluna):
        vistas = {}
        consulta = db.query(coluna)
        if coluna is ProductDB.category:
            consulta = consulta.join(ProductDB.restaurant)  # sem produtos órfãos
        for (valor,) in consulta.distinct():
            for parte in _partes(valor):
                vistas.setdefault(parte.lower(), parte)
        return sorted(vistas.values(), key=str.lower)

    def calcular():
        return Categorias(restaurants=distintas(RestaurantDB.category), products=distintas(ProductDB.category))

    return resposta_com_etag(request, _em_cache(("categories",), calcular))


@router.get("/products", response_model=PaginaProdutos)
def listar_produtos(
    request: Request,
    limit: int = Query(LIMITE_PADRAO, ge=1, le=LIMITE_MAXIMO),
    cursor: Optional[str] = None,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Produtos de todos os restaurantes, `limit` de cada vez, cada um com o resumo do seu
    restaurante (a app abre o cardápio ao tocar)."""
    depois_de = _ler_cursor(cursor)
    categoria = (category or "").strip() or None

    def calcular():
        # JOIN: produtos órfãos (de restaurantes apagados) não entram — também não apareciam antes,
        # quando os produtos vinham dentro de cada restaurante.
        consulta = db.query(ProductDB).join(ProductDB.restaurant).options(contains_eager(ProductDB.restaurant))
        if categoria:
            consulta = consulta.filter(_filtro_aproximado(ProductDB.category, categoria))
        aceita = (lambda p: _tem_categoria(p.category, categoria)) if categoria else (lambda p: True)
        itens, proximo = _paginar(consulta, ProductDB, depois_de, limit, aceita)
        if categoria:
            total = sum(1 for (c,) in db.query(ProductDB.category).join(ProductDB.restaurant).filter(
                _filtro_aproximado(ProductDB.category, categoria)) if _tem_categoria(c, categoria))
        else:
            total = db.query(func.count(ProductDB.id)).join(ProductDB.restaurant).scalar() or 0
        produtos = []
        for p in itens:
            restaurante = RestauranteResumo.model_validate(p.restaurant)
            produtos.append(ProdutoDoCatalogo(
                **Product.model_validate(p).model_dump(),
                restaurant_name=restaurante.name,
                restaurant_plan=restaurante.plan,
                restaurant=restaurante,
            ))
        return PaginaProdutos(items=produtos, next_cursor=proximo, total=total)

    return resposta_com_etag(request, _em_cache(("products", limit, depois_de, categoria), calcular))
