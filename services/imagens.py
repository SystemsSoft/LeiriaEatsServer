# Arquivo: services/imagens.py
"""Versões otimizadas das imagens de restaurantes e produtos (WebP).

Antes guardava-se o ficheiro original tal como vinha: capturas de ecrã de ~700 KB e PNG de
1,2 MB para cartões que a app mostra com ~150 px (medido em 06/11/2026: 43 imagens, 8,4 MB).
Agora cada imagem tem duas versões no bucket:

    Cardapio/<id>-<nome>.webp        grande — até 1200 px, para o detalhe do produto
    Cardapio/<id>-<nome>.mini.webp   miniatura — até 400 px, para listas e cartões

A URL guardada na base de dados é a da grande; a da miniatura obtém-se com url_da_miniatura
(mesmo nome, terminado em ".mini.webp"). Os nomes são únicos e nunca mudam, por isso as
duas vão com cache "imutável": o telemóvel guarda-as e não as volta a pedir.
"""
from io import BytesIO
from typing import Optional, Tuple

from PIL import Image, ImageOps, UnidentifiedImageError

TAMANHO_GRANDE = 1200
TAMANHO_MINIATURA = 400
QUALIDADE_GRANDE = 80
QUALIDADE_MINIATURA = 75
SUFIXO_GRANDE = ".webp"
SUFIXO_MINIATURA = ".mini.webp"
CACHE_IMUTAVEL = "public, max-age=31536000, immutable"


def _webp(imagem: Image.Image, lado_maximo: int, qualidade: int) -> bytes:
    copia = imagem.copy()
    copia.thumbnail((lado_maximo, lado_maximo), Image.Resampling.LANCZOS)  # só reduz, nunca amplia
    saida = BytesIO()
    copia.save(saida, format="WEBP", quality=qualidade, method=6)
    return saida.getvalue()


def gerar_versoes(conteudo: bytes) -> Optional[Tuple[bytes, bytes]]:
    """(grande, miniatura) em WebP, ou None se o conteúdo não for uma imagem que o Pillow abra."""
    try:
        imagem = Image.open(BytesIO(conteudo))
        imagem = ImageOps.exif_transpose(imagem)  # fotos de telemóvel "deitadas" ficam direitas
        imagem.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    tem_transparencia = imagem.mode in ("RGBA", "LA", "PA") or (imagem.mode == "P" and "transparency" in imagem.info)
    imagem = imagem.convert("RGBA" if tem_transparencia else "RGB")
    return (_webp(imagem, TAMANHO_GRANDE, QUALIDADE_GRANDE),
            _webp(imagem, TAMANHO_MINIATURA, QUALIDADE_MINIATURA))


def chaves_das_versoes(chave_original: str) -> Tuple[str, str]:
    """"Cardapio/abc-pizza.png" -> ("Cardapio/abc-pizza.webp", "Cardapio/abc-pizza.mini.webp")."""
    base = chave_original.rsplit(".", 1)[0] if "." in chave_original.rsplit("/", 1)[-1] else chave_original
    if base.endswith(".mini"):
        base = base[: -len(".mini")]
    return base + SUFIXO_GRANDE, base + SUFIXO_MINIATURA


def url_da_miniatura(url: Optional[str]) -> Optional[str]:
    """URL da miniatura de uma imagem otimizada; as outras (ex.: links externos) ficam iguais."""
    if not url or not url.endswith(SUFIXO_GRANDE) or url.endswith(SUFIXO_MINIATURA):
        return url
    return url[: -len(SUFIXO_GRANDE)] + SUFIXO_MINIATURA
