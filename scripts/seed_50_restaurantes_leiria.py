"""
Cria 50 restaurantes fictícios (10 produtos cada) espalhados num raio de 60 km do
centro de Leiria, para popular o catálogo de demonstração/testes.

Regras pedidas:
- 50 restaurantes, 10 categorias diferentes (5 restaurantes por categoria).
- Coordenadas dentro de um raio de 60 km do centro de Leiria (distribuição
  uniforme na área do círculo, não só no raio).
- license="ATIVO", status="ACTIVE" (valores já usados no banco).
- plan: metade "ESSENCE", metade "SMART" (25/25, alternado).
- stripe_account_id fixo (fornecido) + stripe_onboarding_completed=True, para o
  restaurante entrar no filtro de "apto para pagamento" usado pela IA.
- Dados sintéticos: sem imagens reais (image_url fica em branco) e login/senha
  são credenciais de demonstração, não usadas para acesso real.

Uso: python3 scripts/seed_50_restaurantes_leiria.py [--commit]
Sem --commit roda em modo dry-run (mostra o que seria criado, não grava nada).
"""
import argparse
import math
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ulid import ULID
from core.database import SessionLocal
from core.sql_models import RestaurantDB, ProductDB

STRIPE_ACCOUNT_ID = "acct_1U9PQoRQYAJnU5nn"

# Centro de Leiria (Sé de Leiria) e raio pedido
LEIRIA_LAT = 39.7436
LEIRIA_LON = -8.8071
RAIO_KM = 60.0

KM_POR_GRAU_LAT = 111.32

random.seed(42)  # reprodutível — mesma distribuição se rodar de novo em dry-run


def ponto_aleatorio_no_raio(lat0: float, lon0: float, raio_km: float) -> tuple[float, float]:
    """Ponto uniformemente distribuído dentro do círculo (não só na borda)."""
    r = raio_km * math.sqrt(random.random())
    theta = random.uniform(0, 2 * math.pi)
    dx_km = r * math.cos(theta)
    dy_km = r * math.sin(theta)
    dlat = dy_km / KM_POR_GRAU_LAT
    km_por_grau_lon = KM_POR_GRAU_LAT * math.cos(math.radians(lat0))
    dlon = dx_km / km_por_grau_lon
    return round(lat0 + dlat, 6), round(lon0 + dlon, 6)


def slugify(nome: str) -> str:
    tabela = str.maketrans("áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ", "aaaaeeiooouc" + "AAAAEEIOOOUC".lower())
    s = nome.translate(tabela).lower()
    s = "".join(ch if ch.isalnum() else "_" for ch in s)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")[:50]


# ─── Categorias: nome de exibição + 5 nomes de restaurante + menu de 10 produtos ──
CATEGORIAS = [
    {
        "categoria": "Pizzaria",
        "restaurantes": [
            "Pizzaria Bella Leiria", "Forno & Massa", "Pizza do Castelo",
            "Trattoria Vesúvio", "Pizzaria Nonna Sofia",
        ],
        "produtos": [
            ("Pizza Margherita", 9.50, "Molho de tomate, mussarela e manjericão fresco", "tomate, mussarela, manjericão", True),
            ("Pizza Pepperoni", 10.90, "Molho de tomate, mussarela e pepperoni picante", "tomate, mussarela, pepperoni", True),
            ("Pizza Quatro Queijos", 11.50, "Mussarela, gorgonzola, parmesão e provolone", "mussarela, gorgonzola, parmesão, provolone", False),
            ("Pizza Vegetariana", 10.50, "Pimentos, cogumelos, cebola e azeitonas", "pimentos, cogumelos, cebola, azeitonas", False),
            ("Pizza Fiambre e Cogumelos", 10.90, "Molho de tomate, mussarela, fiambre e cogumelos", "tomate, mussarela, fiambre, cogumelos", False),
            ("Calzone Clássico", 9.90, "Massa recheada com fiambre, mussarela e tomate", "fiambre, mussarela, tomate", False),
            ("Bruschetta Tradicional", 5.50, "Pão tostado com tomate, alho e azeite", "pão, tomate, alho, azeite", False),
            ("Esparguete à Bolonhesa", 8.90, "Massa com molho de carne picada e tomate", "esparguete, carne picada, tomate", False),
            ("Tiramisù", 4.50, "Sobremesa italiana com café e mascarpone", "mascarpone, café, cacau", False),
            ("Sumo Natural de Laranja", 3.00, "Sumo de laranja espremido na hora", "laranja", False),
        ],
    },
    {
        "categoria": "Hambúrgueria",
        "restaurantes": [
            "Hamburgueria Central Leiria", "Bife & Brasa", "The Grill House",
            "Hamburgueria do Zé", "Smash Burger Leiria",
        ],
        "produtos": [
            ("Hambúrguer Clássico", 7.50, "Carne bovina, alface, tomate e queijo cheddar", "carne bovina, alface, tomate, cheddar", True),
            ("Hambúrguer Bacon", 8.90, "Carne bovina, bacon crocante e queijo cheddar", "carne bovina, bacon, cheddar", True),
            ("Hambúrguer Duplo", 10.50, "Duas carnes bovinas, queijo e molho especial", "carne bovina, queijo, molho especial", False),
            ("Hambúrguer Vegetariano", 8.50, "Hambúrguer de grão-de-bico, alface e tomate", "grão-de-bico, alface, tomate", False),
            ("Batata Frita Clássica", 3.50, "Porção de batata frita crocante", "batata", False),
            ("Batata Frita com Cheddar e Bacon", 5.00, "Batata frita coberta com cheddar e bacon", "batata, cheddar, bacon", False),
            ("Onion Rings", 4.00, "Anéis de cebola empanados e fritos", "cebola, farinha de trigo", False),
            ("Nuggets de Frango", 5.50, "8 unidades de nuggets de frango", "frango, farinha de trigo", False),
            ("Milkshake de Chocolate", 4.50, "Milkshake cremoso de chocolate", "leite, gelado, chocolate", False),
            ("Refrigerante Lata", 2.00, "Lata de refrigerante 33cl", "água gaseificada, açúcar", False),
        ],
    },
    {
        "categoria": "Japonesa",
        "restaurantes": [
            "Sushi Kobe Leiria", "Tóquio Sushi Bar", "Sakura Sushi",
            "Sushi Yama", "Osaka House",
        ],
        "produtos": [
            ("Combinado Sushi 20 Peças", 16.90, "Seleção variada de sushi e sashimi", "salmão, atum, arroz, alga nori", True),
            ("Uramaki Salmão", 8.50, "8 peças de uramaki de salmão e queijo creme", "salmão, queijo creme, arroz", True),
            ("Uramaki Tempura de Camarão", 9.00, "8 peças de uramaki com camarão tempura", "camarão, arroz, alga nori", False),
            ("Sashimi de Salmão", 9.50, "10 fatias de sashimi de salmão fresco", "salmão", False),
            ("Gyoza de Legumes", 6.00, "6 unidades de gyoza recheados com legumes", "legumes, massa de gyoza", False),
            ("Temaki de Atum", 6.50, "Cone de alga com arroz e atum fresco", "atum, arroz, alga nori", False),
            ("Yakisoba de Frango", 9.90, "Massa salteada com frango e legumes", "frango, massa yakisoba, legumes", False),
            ("Sopa Missoshiru", 3.50, "Sopa tradicional japonesa de missô", "missô, tofu, alga wakame", False),
            ("Harumaki de Legumes", 5.00, "Rolinhos primavera crocantes", "legumes, massa filo", False),
            ("Chá Verde Gelado", 2.50, "Chá verde japonês servido gelado", "chá verde", False),
        ],
    },
    {
        "categoria": "Marisqueira",
        "restaurantes": [
            "Marisqueira Costa de Leiria", "O Pescador", "Marisqueira Atlântico",
            "Cabaz do Mar", "Marisqueira Baía",
        ],
        "produtos": [
            ("Cataplana de Marisco", 22.00, "Mistura de marisco fresco cozinhado em cataplana", "camarão, mexilhão, amêijoas, tomate", True),
            ("Arroz de Marisco", 18.50, "Arroz malandrinho com marisco variado", "arroz, camarão, mexilhão, amêijoas", True),
            ("Amêijoas à Bulhão Pato", 14.00, "Amêijoas salteadas com alho, coentros e vinho branco", "amêijoas, alho, coentros, vinho branco", False),
            ("Lagostins Grelhados", 24.00, "Lagostins frescos grelhados na brasa", "lagostins, azeite, alho", False),
            ("Polvo à Lagareiro", 19.90, "Polvo assado com batata a murro e azeite", "polvo, batata, azeite", False),
            ("Camarão à Guilho", 13.50, "Camarão salteado com alho e piripiri", "camarão, alho, piripiri", False),
            ("Sopa de Peixe", 8.00, "Sopa tradicional de peixe da costa", "peixe, legumes, tomate", False),
            ("Percebes", 16.00, "Percebes frescos cozidos, ao quilo", "percebes", False),
            ("Salada de Polvo", 10.50, "Salada fria de polvo com cebola e pimento", "polvo, cebola, pimento", False),
            ("Vinho Verde (garrafa)", 9.00, "Vinho verde regional, garrafa 75cl", "uva", False),
        ],
    },
    {
        "categoria": "Churrascaria",
        "restaurantes": [
            "Churrascaria Brasa Real", "O Espeto Leiriense", "Churrascaria Gaúcha",
            "Brasa & Fogo", "Churrascaria Sabor do Sul",
        ],
        "produtos": [
            ("Picanha Grelhada", 15.90, "Picanha grelhada na brasa com arroz e farofa", "picanha, arroz, farofa", True),
            ("Espetada de Frango", 9.50, "Espetada de frango grelhado com pimentos", "frango, pimentos, cebola", True),
            ("Costela de Porco", 13.00, "Costela de porco assada lentamente na brasa", "costela de porco", False),
            ("Linguiça Toscana", 7.00, "Linguiça grelhada com pão e mostarda", "linguiça, pão", False),
            ("Frango na Brasa (meio)", 8.50, "Meio frango grelhado com piripiri", "frango, piripiri", False),
            ("Feijoada à Brasileira", 11.00, "Feijoada tradicional com carnes variadas", "feijão preto, carnes variadas, arroz", False),
            ("Farofa de Bacon", 3.50, "Acompanhamento de farofa com bacon", "farinha de mandioca, bacon", False),
            ("Salada Mista", 4.50, "Alface, tomate, cebola e cenoura", "alface, tomate, cebola, cenoura", False),
            ("Pudim de Leite", 3.50, "Sobremesa tradicional de pudim de leite condensado", "leite condensado, ovos", False),
            ("Caipirinha Sem Álcool", 4.00, "Limão, açúcar e gelo", "limão, açúcar", False),
        ],
    },
    {
        "categoria": "Mexicana",
        "restaurantes": [
            "Mexicana Cantina Leiria", "El Sombrero", "Taco Loco",
            "Cantina Azteca", "Guacamole House",
        ],
        "produtos": [
            ("Tacos de Carne (3un)", 8.50, "Tortilhas com carne temperada, alface e queijo", "tortilha, carne, alface, queijo", True),
            ("Burrito de Frango", 9.00, "Tortilha recheada com frango, arroz e feijão", "tortilha, frango, arroz, feijão", True),
            ("Quesadilla de Queijo", 6.50, "Tortilha recheada com queijo derretido", "tortilha, queijo", False),
            ("Nachos com Guacamole", 6.00, "Nachos crocantes com guacamole e queijo", "milho, abacate, queijo", False),
            ("Fajitas de Frango", 10.50, "Tiras de frango grelhado com pimentos e cebola", "frango, pimentos, cebola", False),
            ("Chili con Carne", 8.00, "Ensopado picante de carne e feijão vermelho", "carne, feijão vermelho, malagueta", False),
            ("Enchiladas Verdes", 9.50, "Tortilhas recheadas com molho verde e queijo", "tortilha, molho verde, queijo", False),
            ("Guacamole com Nachos", 5.50, "Puré de abacate fresco com nachos", "abacate, limão, nachos", False),
            ("Churros com Chocolate", 4.50, "Churros polvilhados com açúcar e canela", "farinha, açúcar, canela, chocolate", False),
            ("Limonada Mexicana", 3.00, "Limonada fresca com hortelã", "limão, hortelã", False),
        ],
    },
    {
        "categoria": "Padaria e Pastelaria",
        "restaurantes": [
            "Padaria Central Leiria", "Pastelaria Doce Leiria", "Padaria do Bairro",
            "Pastelaria Flor de Leiria", "Padaria e Pastelaria Sé",
        ],
        "produtos": [
            ("Pastel de Nata", 1.30, "Pastel de nata tradicional português", "massa folhada, creme de ovos", True),
            ("Croissant Misto", 3.20, "Croissant com fiambre e queijo", "croissant, fiambre, queijo", True),
            ("Bola de Berlim", 1.80, "Doce frito recheado com creme", "massa doce, creme", False),
            ("Pão de Deus", 1.50, "Pão doce coberto com coco ralado", "farinha, coco", False),
            ("Broa de Milho", 2.00, "Pão tradicional de milho", "farinha de milho", False),
            ("Sandes de Fiambre e Queijo", 3.00, "Sandes simples de fiambre e queijo", "pão, fiambre, queijo", False),
            ("Bolo de Chocolate (fatia)", 2.80, "Fatia de bolo de chocolate húmido", "chocolate, farinha, ovos", False),
            ("Café Expresso", 0.80, "Café expresso tradicional", "café", False),
            ("Galão", 1.30, "Café com leite servido em copo alto", "café, leite", False),
            ("Sumo de Laranja Natural", 2.50, "Sumo de laranja espremido na hora", "laranja", False),
        ],
    },
    {
        "categoria": "Saudável e Vegetariana",
        "restaurantes": [
            "Green Bowl Leiria", "Vida Saudável Leiria", "Raiz Vegetariana",
            "Fresh & Fit", "Horta Urbana",
        ],
        "produtos": [
            ("Bowl de Quinoa e Grão", 9.50, "Quinoa, grão-de-bico, tomate e abacate", "quinoa, grão-de-bico, tomate, abacate", True),
            ("Salada Caesar Vegetariana", 8.00, "Alface, tomate, croutons e molho caesar", "alface, tomate, croutons", True),
            ("Wrap de Falafel", 7.50, "Wrap com falafel, húmus e legumes", "falafel, húmus, legumes", False),
            ("Hambúrguer de Lentilhas", 8.90, "Hambúrguer vegetal de lentilhas com salada", "lentilhas, alface, tomate", False),
            ("Sopa de Legumes Detox", 4.50, "Sopa de legumes variados", "cenoura, abóbora, courgette", False),
            ("Smoothie Verde", 4.00, "Espinafre, maçã, banana e gengibre", "espinafre, maçã, banana, gengibre", False),
            ("Tigela de Fruta com Granola", 5.50, "Frutas frescas com granola e iogurte", "fruta, granola, iogurte", False),
            ("Húmus com Legumes", 5.00, "Húmus caseiro com palitos de legumes", "grão-de-bico, cenoura, pepino", False),
            ("Chá Gelado de Frutos Vermelhos", 3.00, "Chá gelado natural", "chá, frutos vermelhos", False),
            ("Água de Coco", 3.50, "Água de coco natural", "coco", False),
        ],
    },
    {
        "categoria": "Portuguesa Tradicional",
        "restaurantes": [
            "Tasca do Zé Leiria", "Restaurante O Tacho", "Adega Leiriense",
            "Petiscos da Avó", "Casa do Bacalhau",
        ],
        "produtos": [
            ("Bacalhau à Brás", 12.50, "Bacalhau desfiado com batata palha e ovo", "bacalhau, batata, ovo, cebola", True),
            ("Francesinha", 11.00, "Sandes recheada com carnes e molho especial", "pão, carnes variadas, queijo, molho", True),
            ("Feijoada à Transmontana", 10.50, "Feijoada tradicional com enchidos", "feijão, entrecosto, enchidos", False),
            ("Arroz de Pato", 9.90, "Arroz de pato assado ao estilo tradicional", "pato, arroz, chouriço", False),
            ("Caldo Verde", 4.00, "Sopa tradicional de couve com chouriço", "couve, batata, chouriço", False),
            ("Bitoque", 9.00, "Bife de vaca com ovo, batata frita e arroz", "carne de vaca, ovo, batata", False),
            ("Polvo à Lagareiro", 18.50, "Polvo assado com batata a murro", "polvo, batata, azeite", False),
            ("Leitão à Bairrada", 13.50, "Leitão assado tradicional com batata frita", "leitão, batata", False),
            ("Pastel de Nata (2un)", 2.40, "Dois pastéis de nata tradicionais", "massa folhada, creme de ovos", False),
            ("Vinho Tinto da Casa (copo)", 2.50, "Vinho tinto regional servido ao copo", "uva", False),
        ],
    },
    {
        "categoria": "Sanduíches e Café",
        "restaurantes": [
            "Café Central Leiria", "Sandes & Companhia", "Bistro do Rio",
            "Café Concerto Leiria", "Sanduicheria Leiriense",
        ],
        "produtos": [
            ("Sandes de Frango Grelhado", 5.50, "Sandes de frango grelhado com alface e maionese", "pão, frango, alface, maionese", True),
            ("Tosta Mista", 3.00, "Tosta de fiambre e queijo", "pão, fiambre, queijo", True),
            ("Bagel de Salmão Fumado", 6.50, "Bagel com salmão fumado e queijo creme", "bagel, salmão fumado, queijo creme", False),
            ("Wrap de Atum", 5.80, "Wrap recheado com atum e legumes", "tortilha, atum, legumes", False),
            ("Sandes Vegetariana", 5.00, "Sandes de legumes grelhados e queijo", "pão, legumes grelhados, queijo", False),
            ("Bolo de Cenoura (fatia)", 2.80, "Fatia de bolo de cenoura caseiro", "cenoura, farinha, ovos", False),
            ("Cappuccino", 1.80, "Café expresso com espuma de leite", "café, leite", False),
            ("Chá Preto", 1.50, "Chá preto tradicional", "chá preto", False),
            ("Sumo de Maçã Natural", 2.50, "Sumo de maçã espremido na hora", "maçã", False),
            ("Água Mineral", 1.20, "Garrafa de água mineral 50cl", "água", False),
        ],
    },
]

assert sum(len(c["restaurantes"]) for c in CATEGORIAS) == 50
assert len(CATEGORIAS) == 10
for c in CATEGORIAS:
    assert len(c["produtos"]) == 10, c["categoria"]


def gerar_plano(indice_global: int) -> str:
    return "ESSENCE" if indice_global % 2 == 0 else "SMART"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="Grava de verdade no banco (default: dry-run)")
    args = parser.parse_args()

    db = SessionLocal()
    indice_global = 0
    resumo = []

    try:
        for cat in CATEGORIAS:
            for nome_restaurante in cat["restaurantes"]:
                lat, lon = ponto_aleatorio_no_raio(LEIRIA_LAT, LEIRIA_LON, RAIO_KM)
                login = slugify(nome_restaurante)

                restaurante = RestaurantDB(
                    name=nome_restaurante,
                    phone=f"9{random.randint(10000000, 99999999)}",
                    address=f"Leiria e arredores, Portugal (demo — {round(lat, 4)}, {round(lon, 4)})",
                    category=cat["categoria"],
                    image_url=None,
                    login=login,
                    password="Demo@2026",  # credencial fictícia de demonstração
                    license="ATIVO",
                    plan=gerar_plano(indice_global),
                    latitude=lat,
                    longitude=lon,
                    stripe_account_id=STRIPE_ACCOUNT_ID,
                    stripe_onboarding_completed=True,
                    status="ACTIVE",
                    gid=str(ULID()),
                )
                db.add(restaurante)
                db.flush()  # obtém restaurante.id sem commitar

                for (p_nome, p_preco, p_desc, p_ingredientes, p_popular) in cat["produtos"]:
                    produto = ProductDB(
                        gid=str(ULID()),
                        name=p_nome,
                        description=p_desc,
                        price=p_preco,
                        image_url=None,
                        category=cat["categoria"],
                        restaurant_id=restaurante.id,
                        ingredients=p_ingredientes,
                        is_popular=p_popular,
                        is_available=True,
                    )
                    db.add(produto)

                resumo.append((nome_restaurante, cat["categoria"], gerar_plano(indice_global), lat, lon))
                indice_global += 1

        if args.commit:
            db.commit()
            print(f"✅ {len(resumo)} restaurantes e {len(resumo) * 10} produtos gravados no banco.")
        else:
            db.rollback()
            print(f"🔎 DRY-RUN — {len(resumo)} restaurantes e {len(resumo) * 10} produtos SERIAM criados (nada foi gravado).")
            print("   Rode com --commit para gravar de verdade.\n")

        essence = sum(1 for r in resumo if r[2] == "ESSENCE")
        smart = sum(1 for r in resumo if r[2] == "SMART")
        print(f"   Plano: {essence} ESSENCE / {smart} SMART")
        print(f"   Categorias: {len(CATEGORIAS)}")
        for nome, categoria, plano, lat, lon in resumo[:5]:
            print(f"   - {nome} [{categoria}/{plano}] ({lat}, {lon})")
        print("   ...")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
