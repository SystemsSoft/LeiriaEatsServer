# Arquivo: services/idioma.py
"""Língua de um texto e da chamada de voz — só para escolher o sotaque da voz.

O texto das respostas não depende disto: o Gemini responde no idioma do cliente pela
regra 1 das instruções (gemini_sales_service.py). Aqui decide-se apenas o language_code
da voz: "pt-PT" quando o texto é português (sotaque de Portugal, não do Brasil) e, nos
outros idiomas, nenhum — o Gemini deteta a língua sozinho.
"""
import re
from typing import Optional

_PALAVRAS = {
    "pt": {"não", "você", "olá", "obrigado", "obrigada", "quero", "queria", "para", "com", "uma",
           "um", "os", "as", "do", "da", "dos", "das", "no", "na", "é", "está", "tem", "mais",
           "também", "pedido", "encomenda", "sim", "boa", "bom", "aqui", "isso", "este", "esta",
           "comida", "sobremesa", "bebida", "preço", "favor", "pode", "posso", "gostaria"},
    "en": {"the", "and", "i", "want", "would", "like", "please", "with", "some", "what", "do",
           "you", "have", "hello", "hi", "thanks", "thank", "can", "is", "are", "my", "order",
           "food", "something", "spicy", "cheap", "dessert", "drink", "yes", "no", "it", "for"},
    "es": {"el", "los", "las", "usted", "quiero", "gracias", "hola", "por", "y", "una", "algo",
           "tiene", "comida", "postre", "bebida", "sí", "pedido", "picante", "barato"},
    "fr": {"le", "la", "les", "je", "veux", "voudrais", "merci", "bonjour", "avec", "et", "une",
           "quelque", "chose", "vous", "avez", "dessert", "boisson", "oui", "non", "commande"},
}


def e_portugues(texto: str) -> bool:
    """True se o texto parece português, ou se não há nada que indique outra língua
    (mensagens curtas como "sushi" ou "pizza" ficam em português, o idioma da casa)."""
    t = (texto or "").lower()
    if any(c in t for c in "ãõç"):
        return True
    palavras = set(re.findall(r"[a-zà-öø-ÿ']+", t))
    pontos = {lingua: len(palavras & lista) for lingua, lista in _PALAVRAS.items()}
    outras = max(pontos["en"], pontos["es"], pontos["fr"])
    return pontos["pt"] >= outras


def codigo_voz_para_texto(texto: str) -> Optional[str]:
    return "pt-PT" if e_portugues(texto) else None


def codigo_voz_da_chamada(idioma_do_app: Optional[str]) -> str:
    """language_code da chamada de voz a partir do idioma do telemóvel enviado pela app
    (ex.: "en-US"). Português — de Portugal ou do Brasil — e ausente ficam em "pt-PT"."""
    tag = (idioma_do_app or "").strip()
    if not tag or tag.lower().startswith("pt") or not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", tag):
        return "pt-PT"
    return tag
