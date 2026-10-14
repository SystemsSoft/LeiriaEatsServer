# Arquivo: modules/maria_portuguesa/catalogo.py
#
# Vocabulário fechado do módulo. É a única fonte de verdade: o formulário do perfil
# (via GET /maria-portuguesa/catalogo), a validação dos pedidos, o texto indexado
# para a busca semântica e a declaração da ferramenta do Gemini Live leem daqui.
import unicodedata
from typing import Optional

SERVICOS: dict[str, str] = {
    "limpeza": "Limpeza doméstica",
    "limpeza_profunda": "Limpeza profunda",
    "engomar": "Passar a ferro",
    "roupa": "Tratamento de roupa",
    "cozinhar": "Cozinhar refeições",
    "criancas": "Cuidar de crianças",
    "idosos": "Apoio a idosos",
    "animais": "Cuidar de animais",
    "compras": "Compras e recados",
    "vidros": "Limpeza de vidros",
    "pos_obra": "Limpeza pós-obra",
    "alojamento_local": "Limpeza de alojamento local",
    "organizacao": "Organização da casa",
    "jardim": "Pequena jardinagem",
}

DISTRITOS: list[str] = [
    "Aveiro", "Beja", "Braga", "Bragança", "Castelo Branco", "Coimbra", "Évora",
    "Faro", "Guarda", "Leiria", "Lisboa", "Portalegre", "Porto", "Santarém",
    "Setúbal", "Viana do Castelo", "Vila Real", "Viseu",
    "Região Autónoma dos Açores", "Região Autónoma da Madeira",
]

DIAS: dict[str, str] = {
    "seg": "Segunda-feira",
    "ter": "Terça-feira",
    "qua": "Quarta-feira",
    "qui": "Quinta-feira",
    "sex": "Sexta-feira",
    "sab": "Sábado",
    "dom": "Domingo",
}

PERIODOS: dict[str, str] = {
    "manha": "Manhã",
    "tarde": "Tarde",
    "noite": "Noite",
}

REGIMES: dict[str, str] = {
    "pontual": "Serviço pontual",
    "semanal": "Semanal",
    "quinzenal": "Quinzenal",
    "mensal": "Mensal",
    "interna": "Interna",
}

IDIOMAS: list[str] = [
    "Português", "Inglês", "Espanhol", "Francês", "Ucraniano", "Russo",
    "Crioulo cabo-verdiano", "Romeno", "Hindi", "Nepalês",
]


def normalizar(texto: Optional[str]) -> str:
    """Minúsculas e sem acentos — para comparar o que a pessoa (ou o Gemini) disse
    com o catálogo sem depender de grafia exata ("Santarem" == "Santarém")."""
    if not texto:
        return ""
    sem_acentos = unicodedata.normalize("NFKD", texto)
    sem_acentos = "".join(c for c in sem_acentos if not unicodedata.combining(c))
    return " ".join(sem_acentos.lower().split())


_DISTRITO_POR_CHAVE = {normalizar(d): d for d in DISTRITOS}
_DISTRITO_POR_CHAVE.update({
    "acores": "Região Autónoma dos Açores",
    "madeira": "Região Autónoma da Madeira",
})


def resolver_distrito(texto: Optional[str]) -> Optional[str]:
    """Devolve o nome canónico do distrito, ou None se não reconhecer."""
    chave = normalizar(texto)
    if chave.startswith("distrito de "):
        chave = chave[len("distrito de "):]
    return _DISTRITO_POR_CHAVE.get(chave)


def como_dict() -> dict:
    """Formato servido ao frontend em GET /maria-portuguesa/catalogo."""
    from modules.maria_portuguesa.pagamentos import PRECO_PREMIUM_CENTIMOS

    def pares(mapa: dict[str, str]) -> list[dict]:
        return [{"id": k, "nome": v} for k, v in mapa.items()]

    return {
        "servicos": pares(SERVICOS),
        "distritos": DISTRITOS,
        "dias": pares(DIAS),
        "periodos": pares(PERIODOS),
        "regimes": pares(REGIMES),
        "idiomas": IDIOMAS,
        "preco_premium_centimos": PRECO_PREMIUM_CENTIMOS,
    }
