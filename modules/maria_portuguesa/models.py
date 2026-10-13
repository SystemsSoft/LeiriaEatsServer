# Arquivo: modules/maria_portuguesa/models.py
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, LargeBinary, String, Text
from sqlalchemy.orm import relationship

from modules.maria_portuguesa.database import Base


def _agora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


SENHA_NO_FIREBASE = "firebase"


def _novo_gid() -> str:
    return uuid.uuid4().hex


class MpDomesticaDB(Base):
    """Conta + perfil público de uma empregada doméstica."""
    __tablename__ = "domesticas"

    id = Column(Integer, primary_key=True, index=True)
    # Identificador público. O id numérico nunca sai do servidor, para não permitir
    # enumerar perfis nem deduzir quantas pessoas estão registadas.
    gid = Column(String(32), unique=True, nullable=False, index=True, default=_novo_gid)

    # --- Conta ---
    email = Column(String(255), unique=True, nullable=False, index=True)
    # Contas criadas pelo Firebase Authentication não têm palavra-passe aqui:
    # fica SENHA_NO_FIREBASE, que nunca coincide com um hash scrypt.
    senha_hash = Column(String(255), nullable=False)
    firebase_uid = Column(String(128), unique=True, nullable=True, index=True)
    consentimento_rgpd_em = Column(DateTime, nullable=False, default=_agora)

    # --- Perfil ---
    nome = Column(String(120), nullable=False)
    titulo = Column(String(140), nullable=True)
    sobre = Column(Text, nullable=True)
    foto_url = Column(String(500), nullable=True)

    distrito = Column(String(60), nullable=True, index=True)
    concelho = Column(String(80), nullable=True)

    servicos = Column(JSON, nullable=False, default=list)
    anos_experiencia = Column(Integer, nullable=True)
    preco_hora = Column(Float, nullable=True)

    dias = Column(JSON, nullable=False, default=list)
    periodos = Column(JSON, nullable=False, default=list)
    regimes = Column(JSON, nullable=False, default=list)
    idiomas = Column(JSON, nullable=False, default=list)

    tem_transporte = Column(Boolean, nullable=False, default=False)
    aceita_animais = Column(Boolean, nullable=False, default=False)
    passa_recibo = Column(Boolean, nullable=False, default=False)
    tem_referencias = Column(Boolean, nullable=False, default=False)

    # Contacto: só é devolvido nas rotas públicas se a própria autorizar.
    telemovel = Column(String(30), nullable=True)
    mostrar_contacto = Column(Boolean, nullable=False, default=False)

    # Perfil só aparece na busca quando está publicado.
    publicado = Column(Boolean, nullable=False, default=False, index=True)

    # Vetor E5 (float32 em bytes) do texto do perfil. Guardado na linha para não
    # recalcular todos os perfis a cada arranque do servidor.
    embedding = Column(LargeBinary, nullable=True)

    criado_em = Column(DateTime, nullable=False, default=_agora)
    atualizado_em = Column(DateTime, nullable=False, default=_agora, onupdate=_agora)

    sessoes = relationship("MpSessaoDB", back_populates="domestica", cascade="all, delete-orphan")
    desbloqueios = relationship("MpDesbloqueioDB", back_populates="domestica", cascade="all, delete-orphan")


class MpSessaoDB(Base):
    """Sessão de login. Guarda apenas o SHA-256 do token — quem ler a tabela não
    consegue autenticar-se como ninguém."""
    __tablename__ = "sessoes"

    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    domestica_id = Column(Integer, ForeignKey("domesticas.id", ondelete="CASCADE"), nullable=False, index=True)
    expira_em = Column(DateTime, nullable=False)
    criado_em = Column(DateTime, nullable=False, default=_agora)

    domestica = relationship("MpDomesticaDB", back_populates="sessoes")



class MpDesbloqueioDB(Base):
    """Contacto de uma doméstica pago por quem procura. Quem pagou não tem conta:
    o comprovativo é o id da sessão de pagamento da Stripe, que fica no navegador
    da pessoa — aqui guarda-se só o SHA-256 dele, como nas sessões de login."""
    __tablename__ = "desbloqueios"

    id = Column(Integer, primary_key=True)
    sessao_hash = Column(String(64), unique=True, nullable=False, index=True)
    domestica_id = Column(Integer, ForeignKey("domesticas.id", ondelete="CASCADE"), nullable=False, index=True)
    valor_centimos = Column(Integer, nullable=False)
    # Para encontrar o pagamento no painel da Stripe (reembolsos, reclamações).
    payment_intent = Column(String(64), nullable=True)
    pago_em = Column(DateTime, nullable=False, default=_agora)

    domestica = relationship("MpDomesticaDB", back_populates="desbloqueios")
