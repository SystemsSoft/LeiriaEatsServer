"""
Busca uma imagem real por prato no Wikimedia Commons (repositório gratuito, sem
necessidade de chave de API) e preenche products.image_url para os 500 produtos
criados por scripts/seed_50_restaurantes_leiria.py (restaurant_id >= 13).

Como os 10 restaurantes de cada categoria compartilham o mesmo cardápio de 10
itens, só precisamos buscar 100 imagens (10 categorias x 10 pratos) e aplicar a
mesma imagem a todos os restaurantes daquela categoria com aquele prato.

Uso: python3 scripts/adicionar_imagens_produtos_leiria.py [--commit]
Sem --commit roda em modo dry-run (mostra o que seria gravado, não grava nada).

Nota de licenciamento: as imagens vêm do Wikimedia Commons (CC-BY-SA ou domínio
público, conforme cada arquivo) — adequadas para protótipo/demo, mas confirme a
licença de cada imagem (e dê atribuição se exigido) antes de um lançamento real.
"""
import argparse
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from core.database import SessionLocal
from core.sql_models import ProductDB

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "LeiriaEatsSeedBot/1.0 (uso interno de demonstracao)"}

BLACKLIST_PALAVRAS = (
    "diagram", "map", "logo", "flag", "coat of arms", "chart", "stamp",
    "postcard", "poster", "menu card", "illustration", "drawing", "icon",
    "advertisement", "receipt",
)

# (nome_do_produto_no_banco, termo_de_busca_em_ingles_ou_nome_do_prato)
CATEGORIAS_BUSCA = [
    ("Pizzaria", [
        ("Pizza Margherita", "Margherita pizza"),
        ("Pizza Pepperoni", "pepperoni pizza"),
        ("Pizza Quatro Queijos", "four cheese pizza"),
        ("Pizza Vegetariana", "vegetarian pizza"),
        ("Pizza Fiambre e Cogumelos", "ham mushroom pizza"),
        ("Calzone Clássico", "calzone"),
        ("Bruschetta Tradicional", "bruschetta"),
        ("Esparguete à Bolonhesa", "spaghetti bolognese"),
        ("Tiramisù", "tiramisu"),
        ("Sumo Natural de Laranja", "orange juice glass"),
    ]),
    ("Hambúrgueria", [
        ("Hambúrguer Clássico", "cheeseburger"),
        ("Hambúrguer Bacon", "bacon burger"),
        ("Hambúrguer Duplo", "double cheeseburger"),
        ("Hambúrguer Vegetariano", "veggie burger"),
        ("Batata Frita Clássica", "french fries"),
        ("Batata Frita com Cheddar e Bacon", "loaded fries cheese bacon"),
        ("Onion Rings", "onion rings"),
        ("Nuggets de Frango", "chicken nuggets"),
        ("Milkshake de Chocolate", "chocolate milkshake"),
        ("Refrigerante Lata", "soda can"),
    ]),
    ("Japonesa", [
        ("Combinado Sushi 20 Peças", "sushi platter"),
        ("Uramaki Salmão", "salmon uramaki roll"),
        ("Uramaki Tempura de Camarão", "shrimp tempura roll sushi"),
        ("Sashimi de Salmão", "salmon sashimi"),
        ("Gyoza de Legumes", "vegetable gyoza"),
        ("Temaki de Atum", "tuna temaki"),
        ("Yakisoba de Frango", "yakisoba noodles"),
        ("Sopa Missoshiru", "miso soup"),
        ("Harumaki de Legumes", "spring rolls"),
        ("Chá Verde Gelado", "iced green tea"),
    ]),
    ("Marisqueira", [
        ("Cataplana de Marisco", "cataplana seafood"),
        ("Arroz de Marisco", "seafood rice"),
        ("Amêijoas à Bulhão Pato", "clams white wine garlic"),
        ("Lagostins Grelhados", "grilled prawns"),
        ("Polvo à Lagareiro", "roasted octopus potatoes"),
        ("Camarão à Guilho", "garlic shrimp"),
        ("Sopa de Peixe", "fish soup"),
        ("Percebes", "goose barnacles"),
        ("Salada de Polvo", "octopus salad"),
        ("Vinho Verde (garrafa)", "wine bottle green"),
    ]),
    ("Churrascaria", [
        ("Picanha Grelhada", "picanha grilled steak"),
        ("Espetada de Frango", "chicken skewer"),
        ("Costela de Porco", "pork ribs"),
        ("Linguiça Toscana", "grilled sausage"),
        ("Frango na Brasa (meio)", "grilled chicken piri piri"),
        ("Feijoada à Brasileira", "feijoada"),
        ("Farofa de Bacon", "farofa"),
        ("Salada Mista", "mixed salad"),
        ("Pudim de Leite", "flan caramel pudding"),
        ("Caipirinha Sem Álcool", "virgin caipirinha lime"),
    ]),
    ("Mexicana", [
        ("Tacos de Carne (3un)", "beef tacos"),
        ("Burrito de Frango", "chicken burrito"),
        ("Quesadilla de Queijo", "cheese quesadilla"),
        ("Nachos com Guacamole", "nachos guacamole"),
        ("Fajitas de Frango", "chicken fajitas"),
        ("Chili con Carne", "chili con carne"),
        ("Enchiladas Verdes", "green enchiladas"),
        ("Guacamole com Nachos", "guacamole"),
        ("Churros com Chocolate", "churros chocolate"),
        ("Limonada Mexicana", "limeade mint"),
    ]),
    ("Padaria e Pastelaria", [
        ("Pastel de Nata", "pastel de nata"),
        ("Croissant Misto", "ham cheese croissant"),
        ("Bola de Berlim", "berliner donut"),
        ("Pão de Deus", "coconut sweet bread"),
        ("Broa de Milho", "portuguese corn bread"),
        ("Sandes de Fiambre e Queijo", "ham cheese sandwich"),
        ("Bolo de Chocolate (fatia)", "chocolate cake slice"),
        ("Café Expresso", "espresso coffee cup"),
        ("Galão", "coffee with milk glass"),
        ("Sumo de Laranja Natural", "fresh orange juice"),
    ]),
    ("Saudável e Vegetariana", [
        ("Bowl de Quinoa e Grão", "quinoa chickpea bowl"),
        ("Salada Caesar Vegetariana", "caesar salad"),
        ("Wrap de Falafel", "falafel wrap"),
        ("Hambúrguer de Lentilhas", "lentil burger"),
        ("Sopa de Legumes Detox", "vegetable soup"),
        ("Smoothie Verde", "green smoothie"),
        ("Tigela de Fruta com Granola", "granola fruit bowl"),
        ("Húmus com Legumes", "hummus vegetables"),
        ("Chá Gelado de Frutos Vermelhos", "iced berry tea"),
        ("Água de Coco", "coconut water"),
    ]),
    ("Portuguesa Tradicional", [
        ("Bacalhau à Brás", "bacalhau à brás"),
        ("Francesinha", "francesinha"),
        ("Feijoada à Transmontana", "feijoada"),
        ("Arroz de Pato", "arroz de pato duck rice"),
        ("Caldo Verde", "caldo verde soup"),
        ("Bitoque", "steak egg fries bitoque"),
        ("Polvo à Lagareiro", "polvo à lagareiro octopus"),
        ("Leitão à Bairrada", "roast suckling pig"),
        ("Pastel de Nata (2un)", "pastel de nata"),
        ("Vinho Tinto da Casa (copo)", "red wine glass"),
    ]),
    ("Sanduíches e Café", [
        ("Sandes de Frango Grelhado", "grilled chicken sandwich"),
        ("Tosta Mista", "toasted ham cheese sandwich"),
        ("Bagel de Salmão Fumado", "smoked salmon bagel"),
        ("Wrap de Atum", "tuna wrap"),
        ("Sandes Vegetariana", "vegetable sandwich"),
        ("Bolo de Cenoura (fatia)", "carrot cake slice"),
        ("Cappuccino", "cappuccino"),
        ("Chá Preto", "black tea cup"),
        ("Sumo de Maçã Natural", "apple juice"),
        ("Água Mineral", "mineral water bottle"),
    ]),
]


def _get_com_retry(params: dict, tentativas: int = 5) -> requests.Response:
    """GET com retry/backoff em 429 — a API pública do Wikimedia é sensível a rajadas."""
    for tentativa in range(tentativas):
        resp = requests.get(WIKIMEDIA_API, params=params, headers=HEADERS, timeout=20)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp
        espera = float(resp.headers.get("retry-after", 2)) + tentativa * 2
        print(f"      (429 — aguardando {espera:.0f}s antes de tentar de novo)")
        time.sleep(espera)
    resp.raise_for_status()
    return resp


def buscar_imagem(termo: str) -> str | None:
    """Busca no Wikimedia Commons e devolve a URL direta da primeira imagem válida."""
    try:
        resp = _get_com_retry({
            "action": "query", "list": "search", "srsearch": termo,
            "srnamespace": 6, "format": "json", "srlimit": 6,
        })
        resultados = resp.json().get("query", {}).get("search", [])
        titulos = [r["title"] for r in resultados if not any(p in r["title"].lower() for p in BLACKLIST_PALAVRAS)]
        if not titulos:
            return None

        time.sleep(1.2)
        resp2 = _get_com_retry({
            "action": "query", "titles": "|".join(titulos[:6]),
            "prop": "imageinfo", "iiprop": "url|size|mime", "format": "json",
        })
        paginas = resp2.json().get("query", {}).get("pages", {})

        # Mantém a ordem de relevância da busca original
        info_por_titulo = {}
        for pagina in paginas.values():
            info = pagina.get("imageinfo")
            if info:
                info_por_titulo[pagina.get("title")] = info[0]

        for titulo in titulos:
            info = info_por_titulo.get(titulo)
            if not info:
                continue
            if info.get("mime") not in ("image/jpeg", "image/png"):
                continue
            if info.get("width", 0) < 300:
                continue
            return info["url"]
        return None
    except requests.RequestException as e:
        print(f"   ⚠️  Erro ao buscar '{termo}': {e}")
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="Grava de verdade no banco (default: dry-run)")
    args = parser.parse_args()

    db = SessionLocal()
    total_categorias = len(CATEGORIAS_BUSCA)
    encontrados = 0
    nao_encontrados = []
    atualizacoes = 0

    try:
        for idx_cat, (categoria, produtos) in enumerate(CATEGORIAS_BUSCA, start=1):
            print(f"[{idx_cat}/{total_categorias}] {categoria}")
            for nome_produto, termo_busca in produtos:
                url = buscar_imagem(termo_busca)
                time.sleep(1.2)  # educado com a API pública, sem chave (evita 429)

                if url:
                    encontrados += 1
                    print(f"   ✓ {nome_produto} -> {url}")
                    linhas = (
                        db.query(ProductDB)
                        .filter(ProductDB.restaurant_id >= 13)
                        .filter(ProductDB.category == categoria)
                        .filter(ProductDB.name == nome_produto)
                        .all()
                    )
                    for produto in linhas:
                        produto.image_url = url
                        atualizacoes += 1
                else:
                    nao_encontrados.append(f"{categoria} / {nome_produto} (busca: '{termo_busca}')")
                    print(f"   ✗ {nome_produto} — nenhuma imagem adequada encontrada")

        if args.commit:
            db.commit()
            print(f"\n✅ {atualizacoes} produtos atualizados com imagem ({encontrados}/100 pratos com imagem encontrada).")
        else:
            db.rollback()
            print(f"\n🔎 DRY-RUN — {atualizacoes} produtos SERIAM atualizados ({encontrados}/100 pratos com imagem encontrada). Nada foi gravado.")
            print("   Rode com --commit para gravar de verdade.")

        if nao_encontrados:
            print(f"\n⚠️  {len(nao_encontrados)} prato(s) sem imagem encontrada:")
            for item in nao_encontrados:
                print(f"   - {item}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
