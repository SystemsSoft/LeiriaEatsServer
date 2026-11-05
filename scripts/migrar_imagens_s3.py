"""
Migra as imagens de restaurantes e produtos do bucket antigo (koma-repo) para o
bucket configurado em services/s3_service.py (leiria-eats-bucket, eu-south-2).

Para cada URL do bucket antigo guardada na base de dados:
  1. descarrega a imagem pela URL pública (o bucket antigo é de leitura pública,
     por isso não precisa das credenciais da conta antiga);
  2. envia-a para o bucket novo com a MESMA chave (Restaurants/..., Cardapio/...);
  3. troca a URL nas tabelas restaurants, products e nos pedidos já feitos
     (sub_orders.restaurant_image_url e order_items.image_url).

Uma imagem que falhe no passo 1 ou 2 fica com a URL antiga — nunca se grava uma
URL nova que aponte para um objeto inexistente. Antes de alterar a base de dados é
gravado um CSV com (tabela, id, url_antiga, url_nova) para poder reverter.

Uso:
    python scripts/migrar_imagens_s3.py            simulação: só lista o que seria feito
    python scripts/migrar_imagens_s3.py --aplicar  copia as imagens e atualiza a base de dados
"""
import csv
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import boto3
from sqlalchemy import text

from core.database import engine
from services.s3_service import AWS_ACCESS_KEY, AWS_SECRET_KEY, BUCKET_NAME, REGION_NAME

PREFIXO_ANTIGO = "https://koma-repo.s3.amazonaws.com/"
PREFIXO_NOVO = f"https://{BUCKET_NAME}.s3.{REGION_NAME}.amazonaws.com/"

# (tabela, coluna) que guardam URLs de imagem.
COLUNAS = [
    ("restaurants", "image_url"),
    ("products", "image_url"),
    ("sub_orders", "restaurant_image_url"),
    ("order_items", "image_url"),
]


def _url_codificada(url: str) -> str:
    """As URLs antigas têm espaços crus ("pizza logo 1.jpg"); o urllib precisa deles codificados."""
    partes = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(partes._replace(path=urllib.parse.quote(partes.path)))


def _publica(url: str) -> bool:
    try:
        pedido = urllib.request.Request(_url_codificada(url), method="HEAD")
        with urllib.request.urlopen(pedido, timeout=20) as resposta:
            return resposta.status == 200
    except urllib.error.URLError:
        return False


def _linhas_com_url_antiga(conexao):
    linhas = []
    for tabela, coluna in COLUNAS:
        resultado = conexao.execute(
            text(f"SELECT id, {coluna} FROM {tabela} WHERE {coluna} LIKE :prefixo"),
            {"prefixo": PREFIXO_ANTIGO + "%"},
        )
        linhas.extend((tabela, coluna, id_, url) for id_, url in resultado)
    return linhas


def main():
    aplicar = "--aplicar" in sys.argv[1:]

    with engine.connect() as conexao:
        linhas = _linhas_com_url_antiga(conexao)
    urls = sorted({url for *_, url in linhas})

    print(f"Bucket antigo: {PREFIXO_ANTIGO}")
    print(f"Bucket novo:   {PREFIXO_NOVO}")
    print(f"{len(linhas)} linhas na base de dados, {len(urls)} imagens distintas.")
    for tabela, coluna in COLUNAS:
        total = sum(1 for t, c, *_ in linhas if (t, c) == (tabela, coluna))
        print(f"  {tabela}.{coluna}: {total}")

    if not aplicar:
        print("\nSimulação — nada foi copiado nem alterado. Corra com --aplicar para migrar.")
        return

    config = {"region_name": REGION_NAME}
    if AWS_ACCESS_KEY and AWS_SECRET_KEY:
        config.update(aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY)
    s3 = boto3.client("s3", **config)

    novas = {}  # url antiga -> url nova, só para as imagens copiadas com sucesso
    for i, url in enumerate(urls, 1):
        chave = url[len(PREFIXO_ANTIGO):]
        try:
            with urllib.request.urlopen(_url_codificada(url), timeout=30) as resposta:
                conteudo = resposta.read()
                tipo = resposta.headers.get_content_type()
            if not tipo.startswith("image/"):
                tipo = mimetypes.guess_type(chave)[0] or "image/jpeg"
            s3.put_object(Bucket=BUCKET_NAME, Key=chave, Body=conteudo, ContentType=tipo)
            novas[url] = PREFIXO_NOVO + chave
            print(f"[{i}/{len(urls)}] OK    {chave} ({len(conteudo) // 1024} KB)")
        except Exception as e:
            print(f"[{i}/{len(urls)}] FALHA {chave}: {e}")

    if not novas:
        print("\nNenhuma imagem copiada — a base de dados não foi alterada.")
        sys.exit(1)

    a_alterar = [(t, c, id_, url, novas[url]) for t, c, id_, url in linhas if url in novas]
    backup = f"migracao_imagens_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    with open(backup, "w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(["tabela", "coluna", "id", "url_antiga", "url_nova"])
        escritor.writerows(a_alterar)
    print(f"\nBackup das URLs antigas gravado em {os.path.abspath(backup)}")

    with engine.begin() as conexao:
        for tabela, coluna, id_, antiga, nova in a_alterar:
            conexao.execute(
                text(f"UPDATE {tabela} SET {coluna} = :nova WHERE id = :id AND {coluna} = :antiga"),
                {"nova": nova, "id": id_, "antiga": antiga},
            )
    print(f"{len(a_alterar)} linhas atualizadas na base de dados.")

    falhas = len(urls) - len(novas)
    if falhas:
        print(f"Atenção: {falhas} imagens falharam e ficaram com a URL antiga.")

    exemplo = next(iter(novas.values()))
    if _publica(exemplo):
        print("As imagens novas abrem publicamente.")
    else:
        print(f"Atenção: {exemplo} não abre publicamente — falta a bucket policy de leitura pública "
              f"em {BUCKET_NAME} (Restaurants/* e Cardapio/*).")


if __name__ == "__main__":
    main()
