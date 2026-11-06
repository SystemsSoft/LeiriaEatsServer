"""
Converte as imagens já existentes de restaurantes e produtos para as versões otimizadas em
WebP (grande até 1200 px + miniatura até 400 px, com cache imutável — services/imagens.py).

Para cada URL do bucket guardada na base de dados que ainda não seja uma imagem otimizada:
  1. descarrega o original pela URL pública;
  2. envia as duas versões com a mesma função dos uploads novos (upload_file_to_s3), que lhes
     dá nomes NOVOS — o original fica intacto no bucket;
  3. troca a URL nas tabelas restaurants, products e nos pedidos já feitos
     (sub_orders.restaurant_image_url e order_items.image_url).

Antes de alterar a base de dados grava um CSV com (tabela, coluna, id, url_antiga, url_nova)
para poder reverter. Uma imagem que falhe fica com a URL antiga.

Uso (no servidor, onde estão as credenciais do bucket):
    python scripts/otimizar_imagens.py            simulação: só lista o que seria feito
    python scripts/otimizar_imagens.py --aplicar  converte e atualiza a base de dados
"""
import csv
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from core.database import engine
from services.imagens import SUFIXO_GRANDE, SUFIXO_MINIATURA, url_da_miniatura
from services.s3_service import BUCKET_NAME, REGION_NAME, upload_file_to_s3

PREFIXO = f"https://{BUCKET_NAME}.s3.{REGION_NAME}.amazonaws.com/"
COLUNAS = [
    ("restaurants", "image_url"),
    ("products", "image_url"),
    ("sub_orders", "restaurant_image_url"),
    ("order_items", "image_url"),
]


def _url_codificada(url: str) -> str:
    """URLs antigas têm espaços crus ("pizza logo 1.jpg"); o urllib precisa deles codificados."""
    partes = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(partes._replace(path=urllib.parse.quote(partes.path)))


def _existe(url: str) -> bool:
    try:
        with urllib.request.urlopen(urllib.request.Request(_url_codificada(url), method="HEAD"), timeout=20) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _ja_otimizada(url: str) -> bool:
    return url.endswith(SUFIXO_GRANDE) and not url.endswith(SUFIXO_MINIATURA) and _existe(url_da_miniatura(url))


def main():
    aplicar = "--aplicar" in sys.argv[1:]
    with engine.connect() as conexao:
        linhas = []
        for tabela, coluna in COLUNAS:
            for id_, url in conexao.execute(
                text(f"SELECT id, {coluna} FROM {tabela} WHERE {coluna} LIKE :prefixo"),
                {"prefixo": PREFIXO + "%"},
            ):
                linhas.append((tabela, coluna, id_, url))
    urls = sorted({url for *_, url in linhas})
    por_converter = [u for u in urls if not _ja_otimizada(u)]

    print(f"{len(linhas)} linhas na base de dados, {len(urls)} imagens distintas, {len(por_converter)} por converter.")
    if not aplicar:
        print("Simulação — nada foi enviado nem alterado. Corra com --aplicar para converter.")
        return

    novas, antes_kb, depois_kb = {}, 0, 0
    for i, url in enumerate(por_converter, 1):
        chave = url[len(PREFIXO):]
        pasta, nome = chave.split("/", 1) if "/" in chave else ("Cardapio", chave)
        nome = re.sub(r"^[0-9a-f]{12}-", "", nome)  # sem o prefixo único de um upload anterior
        try:
            with urllib.request.urlopen(_url_codificada(url), timeout=30) as r:
                original = r.read()
            nova = upload_file_to_s3(BytesIO(original), nome, folder=pasta)
            if not nova or not nova.endswith(SUFIXO_GRANDE):
                raise RuntimeError("não foi possível gerar a versão WebP")
            with urllib.request.urlopen(_url_codificada(nova), timeout=30) as r:
                tamanho_grande = len(r.read())
            with urllib.request.urlopen(_url_codificada(url_da_miniatura(nova)), timeout=30) as r:
                tamanho_mini = len(r.read())
            novas[url] = nova
            antes_kb += len(original) / 1024
            depois_kb += tamanho_mini / 1024
            print(f"[{i}/{len(por_converter)}] OK    {nome[:50]:<50} {len(original)//1024:5d} KB -> "
                  f"grande {tamanho_grande//1024} KB, miniatura {tamanho_mini//1024} KB")
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(por_converter)}] FALHA {nome[:50]}: {e}")

    if not novas:
        print("Nenhuma imagem convertida — a base de dados não foi alterada.")
        sys.exit(1)

    a_alterar = [(t, c, id_, url, novas[url]) for t, c, id_, url in linhas if url in novas]
    backup = f"otimizacao_imagens_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    with open(backup, "w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(["tabela", "coluna", "id", "url_antiga", "url_nova"])
        escritor.writerows(a_alterar)
    print(f"\nBackup das URLs antigas: {os.path.abspath(backup)}")

    with engine.begin() as conexao:
        for tabela, coluna, id_, antiga, nova in a_alterar:
            conexao.execute(text(f"UPDATE {tabela} SET {coluna} = :nova WHERE id = :id AND {coluna} = :antiga"),
                            {"nova": nova, "id": id_, "antiga": antiga})
    print(f"{len(a_alterar)} linhas atualizadas. Originais: {antes_kb/1024:.1f} MB -> miniaturas: {depois_kb/1024:.1f} MB.")
    falhas = len(por_converter) - len(novas)
    if falhas:
        print(f"Atenção: {falhas} imagens falharam e ficaram com a URL antiga.")


if __name__ == "__main__":
    main()
