# Arquivo: modules/maria_portuguesa/schemas.py
import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from modules.maria_portuguesa import catalogo
from modules.maria_portuguesa.models import MpDomesticaDB, premium_ativo

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TELEMOVEL_RE = re.compile(r"^\+?[0-9 ]{9,20}$")


def _limpar(valor: Optional[str]) -> Optional[str]:
    if valor is None:
        return None
    valor = valor.strip()
    return valor or None


def _validar_lista(valores: Optional[list[str]], permitidos, campo: str) -> Optional[list[str]]:
    if valores is None:
        return None
    invalidos = [v for v in valores if v not in permitidos]
    if invalidos:
        raise ValueError(f"{campo} inválido(s): {', '.join(invalidos)}")
    # Remove repetidos preservando a ordem do catálogo.
    return [v for v in permitidos if v in valores]


class RegistoRequest(BaseModel):
    nome: str = Field(min_length=2, max_length=120)
    email: str = Field(max_length=255)
    senha: str = Field(min_length=8, max_length=128)
    consentimento_rgpd: bool

    @field_validator("nome")
    @classmethod
    def _nome(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Indique o seu nome.")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Endereço de email inválido.")
        return v

    @field_validator("consentimento_rgpd")
    @classmethod
    def _consentimento(cls, v: bool) -> bool:
        if not v:
            raise ValueError("É necessário aceitar o tratamento dos dados para criar o perfil.")
        return v


class LoginRequest(BaseModel):
    email: str
    senha: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return v.strip().lower()


class EntrarFirebaseRequest(BaseModel):
    """Sessão a partir de um ID token do Firebase. O nome e o consentimento só
    vêm (e só são precisos) quando é para criar o perfil."""
    id_token: str = Field(min_length=1, max_length=4096)
    nome: Optional[str] = Field(default=None, max_length=120)
    consentimento_rgpd: bool = False

    @field_validator("nome")
    @classmethod
    def _nome(cls, v: Optional[str]) -> Optional[str]:
        v = _limpar(v)
        if v is not None and len(v) < 2:
            raise ValueError("Indique o seu nome.")
        return v


class RetornoRequest(BaseModel):
    # Página onde a doméstica está, para a Stripe a devolver ao mesmo sítio.
    retorno: Optional[str] = Field(default=None, max_length=500)


class ConfirmarPremiumRequest(BaseModel):
    # Id da sessão de pagamento da Stripe (cs_...), tal como voltou no endereço.
    sessao: str = Field(min_length=8, max_length=255)


class PerfilUpdate(BaseModel):
    """Atualização parcial: só os campos enviados são alterados."""
    nome: Optional[str] = Field(default=None, max_length=120)
    titulo: Optional[str] = Field(default=None, max_length=140)
    sobre: Optional[str] = Field(default=None, max_length=2000)
    distrito: Optional[str] = None
    concelho: Optional[str] = Field(default=None, max_length=80)
    servicos: Optional[list[str]] = None
    anos_experiencia: Optional[int] = Field(default=None, ge=0, le=60)
    preco_hora: Optional[float] = Field(default=None, ge=0, le=200)
    dias: Optional[list[str]] = None
    periodos: Optional[list[str]] = None
    regimes: Optional[list[str]] = None
    idiomas: Optional[list[str]] = None
    tem_transporte: Optional[bool] = None
    aceita_animais: Optional[bool] = None
    passa_recibo: Optional[bool] = None
    tem_referencias: Optional[bool] = None
    telemovel: Optional[str] = Field(default=None, max_length=30)
    mostrar_contacto: Optional[bool] = None
    publicado: Optional[bool] = None

    @field_validator("nome", "titulo", "sobre", "concelho", "telemovel")
    @classmethod
    def _texto(cls, v: Optional[str]) -> Optional[str]:
        return _limpar(v)

    @field_validator("telemovel")
    @classmethod
    def _telemovel(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _TELEMOVEL_RE.match(v):
            raise ValueError("Número de telemóvel inválido.")
        return v

    @field_validator("distrito")
    @classmethod
    def _distrito(cls, v: Optional[str]) -> Optional[str]:
        v = _limpar(v)
        if v is None:
            return None
        canonico = catalogo.resolver_distrito(v)
        if canonico is None:
            raise ValueError("Distrito desconhecido.")
        return canonico

    @field_validator("servicos")
    @classmethod
    def _servicos(cls, v):
        return _validar_lista(v, catalogo.SERVICOS, "Serviço")

    @field_validator("dias")
    @classmethod
    def _dias(cls, v):
        return _validar_lista(v, catalogo.DIAS, "Dia")

    @field_validator("periodos")
    @classmethod
    def _periodos(cls, v):
        return _validar_lista(v, catalogo.PERIODOS, "Período")

    @field_validator("regimes")
    @classmethod
    def _regimes(cls, v):
        return _validar_lista(v, catalogo.REGIMES, "Regime")

    @field_validator("idiomas")
    @classmethod
    def _idiomas(cls, v):
        return _validar_lista(v, catalogo.IDIOMAS, "Idioma")


class BuscaRequest(BaseModel):
    consulta: str = Field(default="", max_length=500)
    # Texto livre: concelho, cidade ou distrito ("Leiria", "Marinha Grande").
    localidade: Optional[str] = Field(default=None, max_length=80)
    distrito: Optional[str] = None
    servicos: Optional[list[str]] = None
    dias: Optional[list[str]] = None
    preco_max: Optional[float] = Field(default=None, ge=0)
    limite: int = Field(default=6, ge=1, le=24)


# ── Saída ────────────────────────────────────────────────────────────────────

# O que falta para o perfil poder ser publicado (campo → texto mostrado à pessoa).
_OBRIGATORIOS_PUBLICACAO = {
    "distrito": "o distrito",
    "concelho": "o concelho",
    "servicos": "pelo menos um serviço",
    "sobre": "uma apresentação",
}


def campos_em_falta(d: MpDomesticaDB) -> list[str]:
    return [campo for campo in _OBRIGATORIOS_PUBLICACAO if not getattr(d, campo)]


def descrever_em_falta(campos: list[str]) -> str:
    return ", ".join(_OBRIGATORIOS_PUBLICACAO[c] for c in campos)


def perfil_publico(d: MpDomesticaDB) -> dict:
    """Perfil tal como qualquer visitante o vê. Nunca inclui email; o telemóvel só
    sai se estiver registado e a própria tiver autorizado mostrá-lo."""
    return {
        "gid": d.gid,
        "nome": d.nome,
        "titulo": d.titulo,
        "sobre": d.sobre,
        "foto_url": d.foto_url,
        "distrito": d.distrito,
        "concelho": d.concelho,
        "servicos": list(d.servicos or []),
        "anos_experiencia": d.anos_experiencia,
        "preco_hora": d.preco_hora,
        "dias": list(d.dias or []),
        "periodos": list(d.periodos or []),
        "regimes": list(d.regimes or []),
        "idiomas": list(d.idiomas or []),
        "tem_transporte": bool(d.tem_transporte),
        "aceita_animais": bool(d.aceita_animais),
        "passa_recibo": bool(d.passa_recibo),
        "tem_referencias": bool(d.tem_referencias),
        "telemovel": d.telemovel.strip() if tem_contacto(d) else None,
        "contacto_disponivel": tem_contacto(d),
        # Assinatura Premium ativa: o perfil sobe nas pesquisas e leva o selo.
        "destaque": premium_ativo(d),
    }


def motivo_sem_contacto(d: MpDomesticaDB) -> Optional[str]:
    """Porque é que o contacto desta pessoa não pode ser mostrado;
    None quando pode. As duas condições: ter um telemóvel registado e ter
    autorizado que o vejam."""
    if not _TELEMOVEL_RE.match((d.telemovel or "").strip()):
        return "sem_contacto"
    if not d.mostrar_contacto:
        return "nao_partilhado"
    return None


def tem_contacto(d: MpDomesticaDB) -> bool:
    return motivo_sem_contacto(d) is None


def perfil_privado(d: MpDomesticaDB) -> dict:
    """Perfil tal como a própria o vê no editor."""
    dados = perfil_publico(d)
    dados.update({
        "email": d.email,
        "telemovel": d.telemovel,
        "mostrar_contacto": bool(d.mostrar_contacto),
        "publicado": bool(d.publicado),
        "em_falta": campos_em_falta(d),
        "premium_ate": d.premium_ate.isoformat() + "Z" if premium_ativo(d) else None,
        "premium_renova": bool(d.premium_renova) and premium_ativo(d),
    })
    return dados
