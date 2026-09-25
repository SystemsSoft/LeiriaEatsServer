"""
Testes do nome dos arquivos enviados ao S3 (services/s3_service.py) — sem rede: boto3 é um dublê.

Bug (24/09/2026): a chave do objeto era o nome ORIGINAL do arquivo, então dois restaurantes que subiam
"images.jpeg" gravavam no MESMO objeto — o 2º sobrescrevia o 1º (a Mexicana passou a mostrar o logo da
Domino's e os dois cadastros ficaram com a mesma URL).

Execução:
    python3 tests/test_s3_upload_nome_unico.py
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.s3_service as s3


class _ClienteFake:
    def __init__(self):
        self.envios = []

    def upload_fileobj(self, fileobj, bucket, key, ExtraArgs=None):
        self.envios.append((bucket, key, ExtraArgs))


def _enviar(filename, folder="Restaurants"):
    fake = _ClienteFake()
    original = s3.boto3.client
    s3.boto3.client = lambda *a, **k: fake
    try:
        url = s3.upload_file_to_s3(io.BytesIO(b"x"), filename, folder=folder)
    finally:
        s3.boto3.client = original
    return url, fake.envios[0]


def teste_mesmo_nome_de_arquivo_gera_chaves_diferentes():
    url1, (_, key1, _) = _enviar("images.jpeg")
    url2, (_, key2, _) = _enviar("images.jpeg")
    assert key1 != key2 and url1 != url2, "dois uploads de 'images.jpeg' caíram na mesma chave (o 2º sobrescreve o 1º)"
    assert key1.startswith("Restaurants/") and key1.endswith("-images.jpeg"), key1
    print("OK  - dois uploads de 'images.jpeg' não usam mais a mesma chave")


def teste_url_devolvida_aponta_para_a_chave_gravada():
    url, (bucket, key, _) = _enviar("logo.png", folder="Cardapio")
    assert url == f"https://{bucket}.s3.amazonaws.com/{key}" and key.startswith("Cardapio/"), (url, key)
    print("OK  - a URL devolvida corresponde à chave gravada (pasta respeitada)")


def teste_nome_higienizado_sem_espaco_nem_caminho():
    _, (_, key, _) = _enviar("../../pizza logo 1 (final).JPG")
    nome = key.split("/", 1)[1]
    assert re.fullmatch(r"[0-9a-f]{12}-[A-Za-z0-9._-]+\.jpg", nome), nome
    assert " " not in key and ".." not in key and key.count("/") == 1, key
    print("OK  - espaços, parênteses e '../' são removidos do nome; extensão em minúsculas")


def teste_nome_ausente_ou_estranho_usa_padrao():
    for fn in (None, "", "sem_extensao", "arquivo.tiff.exe.enorme_demais"):
        _, (_, key, _) = _enviar(fn)
        assert re.fullmatch(r"Restaurants/[0-9a-f]{12}-[A-Za-z0-9._-]+\.[a-z0-9]{1,5}", key), (fn, key)
    print("OK  - nome ausente/sem extensão/estranho cai num padrão seguro")


def teste_content_type_segue_a_extensao():
    _, (_, _, extra) = _enviar("a.png"); assert extra["ContentType"] == "image/png", extra
    _, (_, _, extra) = _enviar("a.webp"); assert extra["ContentType"] == "image/webp", extra
    _, (_, _, extra) = _enviar("a.jpeg"); assert extra["ContentType"] == "image/jpeg", extra
    _, (_, _, extra) = _enviar("a.pdf"); assert extra["ContentType"] == "image/jpeg", "não-imagem cai no padrão"
    print("OK  - ContentType acompanha a extensão (antes era sempre image/jpeg)")


if __name__ == "__main__":
    teste_mesmo_nome_de_arquivo_gera_chaves_diferentes()
    teste_url_devolvida_aponta_para_a_chave_gravada()
    teste_nome_higienizado_sem_espaco_nem_caminho()
    teste_nome_ausente_ou_estranho_usa_padrao()
    teste_content_type_segue_a_extensao()
    print("\nTodos os testes do upload para o S3 passaram.")
