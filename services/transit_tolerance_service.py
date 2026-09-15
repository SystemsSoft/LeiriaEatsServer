"""
Tolerância de trânsito (PLANO_RECOLHA_MULTI_RESTAURANTE.md, secção 4.4): quantos minutos um
prato aguenta fora do restaurante mantendo qualidade aceitável (gelado derrete, chocolate
quente esfria, sopa perde o ponto).

A FONTE do dado é o campo `ProductDB.transit_tolerance_minutes`, preenchido pelo restaurante
no cadastro/edição do produto (obrigatório no formulário e na API de escrita). Este módulo é
só o FALLBACK: usado no backfill dos produtos já cadastrados e como rede de segurança para
quando o campo vier vazio (produto antigo ainda não editado, ou item de pedido sem
product_gid resolvido).

Nunca chamar isto como se fosse a fonte de verdade — ver resolve_transit_tolerance().
"""
import re
import unicodedata

# Minutos por categoria, aplicados sobre o nome NORMALIZADO (ver _normalizar_categoria).
# Cobre as categorias em uso em produção (ver levantamento do PLANO_RECOLHA_MULTI_RESTAURANTE.md,
# secção 4.4) — inclui variações de grafia (ex.: "pizza" e "pizzaria") porque o cadastro
# existente não é consistente.
TOLERANCIA_POR_CATEGORIA_NORMALIZADA: dict[str, int] = {
    "gelataria": 12,
    "sobremesa": 20,
    "sobremesas": 20,
    "cafe": 15,
    "bebidas": 20,
    "padaria e pastelaria": 20,
    "sushi": 25,
    "japonesa": 25,
    "marisqueira": 25,
    "saudavel e vegetariana": 25,
    "saudavel / saladas": 25,
    "sanduiches e cafe": 25,
    "macarrao oriental": 30,
    "mexicana": 30,
    "burrito": 30,
    "portuguesa tradicional": 30,
    "sopas": 20,
    "churrascaria": 35,
    "hamburgueria": 30,
    "hamburguer": 30,
    "pizzaria": 35,
    "pizza": 35,
    "combo": 30,
    "combos": 30,
    "caixa surpresa": 30,
}

# Quando a categoria não mapeia para nada acima (ex.: "Outros").
TOLERANCIA_FALLBACK_PADRAO = 40

# Limites de validação — mesmo intervalo usado no formulário do KomaRestaurant.
TOLERANCIA_MIN = 5
TOLERANCIA_MAX = 120


def _normalizar_categoria(categoria: str | None) -> str:
    """minúsculas, sem acentos, espaços colapsados — pra 'Japonesa'/'japonesa'/'JAPONESA '
    caírem na mesma chave."""
    if not categoria:
        return ""
    texto = unicodedata.normalize("NFKD", categoria).encode("ascii", "ignore").decode("ascii")
    texto = texto.strip().lower()
    texto = re.sub(r"\s+", " ", texto)
    return texto


def tolerancia_padrao_por_categoria(categoria: str | None) -> int:
    """Fallback por categoria — usar só quando não há transit_tolerance_minutes no produto."""
    chave = _normalizar_categoria(categoria)
    return TOLERANCIA_POR_CATEGORIA_NORMALIZADA.get(chave, TOLERANCIA_FALLBACK_PADRAO)


def resolve_transit_tolerance(transit_tolerance_minutes: int | None, categoria: str | None) -> int:
    """Resolve a tolerância efetiva de um produto: valor cadastrado, com fallback por
    categoria só quando o campo estiver vazio. É esta função — não o dict acima diretamente —
    que o resto do backend deve chamar."""
    if transit_tolerance_minutes is not None and transit_tolerance_minutes > 0:
        return transit_tolerance_minutes
    return tolerancia_padrao_por_categoria(categoria)
