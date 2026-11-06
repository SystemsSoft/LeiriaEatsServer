"""
Testes das imagens otimizadas (services/imagens.py e o upload em services/s3_service.py) —
sem rede: o S3 é um dublê.

Execução:
    python3 tests/test_imagens.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

import services.s3_service as s3
from services.imagens import (CACHE_IMUTAVEL, TAMANHO_GRANDE, TAMANHO_MINIATURA, chaves_das_versoes,
                              gerar_versoes, url_da_miniatura)


def _imagem(largura, altura, modo="RGB", formato="PNG"):
    saida = io.BytesIO()
    Image.new(modo, (largura, altura), (200, 80, 40, 128) if modo == "RGBA" else (200, 80, 40)).save(saida, format=formato)
    return saida.getvalue()


def teste_gera_grande_e_miniatura_em_webp_mais_pequenas():
    original = _imagem(3000, 2000, formato="PNG")
    grande, mini = gerar_versoes(original)
    for dados, lado in ((grande, TAMANHO_GRANDE), (mini, TAMANHO_MINIATURA)):
        img = Image.open(io.BytesIO(dados))
        assert img.format == "WEBP" and max(img.size) == lado, (img.format, img.size)
    assert Image.open(io.BytesIO(mini)).size == (400, 267)  # mantém a proporção
    assert len(mini) < len(grande) < len(original)
    print("OK  - gera grande (1200 px) e miniatura (400 px) em WebP, mantendo a proporção")


def teste_imagem_pequena_nao_e_ampliada_e_transparencia_fica():
    grande, mini = gerar_versoes(_imagem(300, 200, modo="RGBA"))
    for dados in (grande, mini):
        img = Image.open(io.BytesIO(dados))
        assert img.size == (300, 200) and img.mode == "RGBA", (img.size, img.mode)
    print("OK  - imagem pequena não é ampliada e PNG com transparência continua transparente")


def teste_nao_imagem_devolve_none():
    assert gerar_versoes(b"isto nao e uma imagem") is None
    print("OK  - conteúdo que não é imagem devolve None (upload segue com o original)")


def teste_nomes_e_url_da_miniatura():
    assert chaves_das_versoes("Cardapio/abc-pizza logo.png") == ("Cardapio/abc-pizza logo.webp", "Cardapio/abc-pizza logo.mini.webp")
    assert chaves_das_versoes("Cardapio/abc-soba.webp") == ("Cardapio/abc-soba.webp", "Cardapio/abc-soba.mini.webp")
    base = "https://leiria-eats-bucket.s3.eu-south-2.amazonaws.com/Cardapio/abc-pizza"
    assert url_da_miniatura(base + ".webp") == base + ".mini.webp"
    assert url_da_miniatura(base + ".mini.webp") == base + ".mini.webp"
    assert url_da_miniatura(base + ".jpg") == base + ".jpg"  # imagem antiga, ainda sem miniatura
    assert url_da_miniatura(None) is None
    print("OK  - chaves .webp/.mini.webp e URL da miniatura (as antigas ficam iguais)")


def teste_upload_guarda_as_duas_versoes_com_cache_imutavel():
    class _ClienteFake:
        def __init__(self):
            self.objetos = {}

        def put_object(self, Bucket, Key, Body, ContentType, CacheControl):
            self.objetos[Key] = (ContentType, CacheControl, len(Body))

    fake = _ClienteFake()
    original = s3.boto3.client
    s3.boto3.client = lambda *a, **k: fake
    try:
        url = s3.upload_file_to_s3(io.BytesIO(_imagem(2400, 1600, formato="JPEG")), "Foto do prato.JPG", folder="Cardapio")
    finally:
        s3.boto3.client = original
    chaves = sorted(fake.objetos)
    assert len(chaves) == 2 and chaves[0].endswith(".mini.webp") and chaves[1].endswith(".webp"), chaves
    assert all(tipo == "image/webp" and cache == CACHE_IMUTAVEL for tipo, cache, _ in fake.objetos.values())
    assert url.endswith(chaves[1]) and url.endswith(".webp") and not url.endswith(".mini.webp"), url
    print("OK  - upload guarda grande e miniatura em WebP com cache imutável e devolve a URL da grande")


if __name__ == "__main__":
    teste_gera_grande_e_miniatura_em_webp_mais_pequenas()
    teste_imagem_pequena_nao_e_ampliada_e_transparencia_fica()
    teste_nao_imagem_devolve_none()
    teste_nomes_e_url_da_miniatura()
    teste_upload_guarda_as_duas_versoes_com_cache_imutavel()
    print("\nTodos os testes de imagens passaram.")
