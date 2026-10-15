# Arquivo: modules/maria_portuguesa/models.py
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, JSON, LargeBinary, String, Text, UniqueConstraint,
)
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

    # --- Recebimentos (Stripe Connect) ---
    # Conta Express onde recebe o que as famílias pagam pelos acordos.
    stripe_conta_id = Column(String(64), nullable=True, index=True)
    # A Stripe já confirmou a identidade e o IBAN: pode receber transferências.
    recebimentos_ativos = Column(Boolean, nullable=False, default=False)

    # Vetor E5 (float32 em bytes) do texto do perfil. Guardado na linha para não
    # recalcular todos os perfis a cada arranque do servidor.
    embedding = Column(LargeBinary, nullable=True)

    criado_em = Column(DateTime, nullable=False, default=_agora)
    atualizado_em = Column(DateTime, nullable=False, default=_agora, onupdate=_agora)

    sessoes = relationship("MpSessaoDB", back_populates="domestica", cascade="all, delete-orphan")
    # Quando a conta é apagada, sai também das listas de quem a tinha guardado.
    guardada_por = relationship("MpFavoritoDB", back_populates="domestica", cascade="all, delete-orphan")
    # Os acordos ficam (são registos de pagamentos), só perdem a ligação à conta.
    acordos = relationship("MpAcordoDB", back_populates="domestica")


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




class MpFamiliaDB(Base):
    """Conta de quem procura uma doméstica. Só guarda o essencial: quem é, onde
    procura e as domésticas que guardou. Entra pelo Firebase Authentication."""
    __tablename__ = "familias"

    id = Column(Integer, primary_key=True)
    gid = Column(String(32), unique=True, nullable=False, index=True, default=_novo_gid)
    firebase_uid = Column(String(128), unique=True, nullable=False, index=True)
    email = Column(String(255), nullable=False)
    consentimento_rgpd_em = Column(DateTime, nullable=False, default=_agora)

    nome = Column(String(120), nullable=False)
    # Zona onde procura ajuda: serve de ponto de partida às pesquisas.
    distrito = Column(String(60), nullable=True)
    concelho = Column(String(80), nullable=True)

    criado_em = Column(DateTime, nullable=False, default=_agora)
    atualizado_em = Column(DateTime, nullable=False, default=_agora, onupdate=_agora)

    sessoes = relationship("MpSessaoFamiliaDB", back_populates="familia", cascade="all, delete-orphan")
    favoritos = relationship("MpFavoritoDB", back_populates="familia", cascade="all, delete-orphan",
                             order_by="MpFavoritoDB.criado_em.desc()")
    acordos = relationship("MpAcordoDB", back_populates="familia")


class MpSessaoFamiliaDB(Base):
    """Sessão de login de uma família. Como nas das domésticas, só o SHA-256 do token."""
    __tablename__ = "sessoes_familias"

    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    familia_id = Column(Integer, ForeignKey("familias.id", ondelete="CASCADE"), nullable=False, index=True)
    expira_em = Column(DateTime, nullable=False)
    criado_em = Column(DateTime, nullable=False, default=_agora)

    familia = relationship("MpFamiliaDB", back_populates="sessoes")


class MpFavoritoDB(Base):
    """Doméstica que uma família guardou para contactar mais tarde."""
    __tablename__ = "favoritos"
    __table_args__ = (UniqueConstraint("familia_id", "domestica_id", name="uq_favoritos_familia_domestica"),)

    id = Column(Integer, primary_key=True)
    familia_id = Column(Integer, ForeignKey("familias.id", ondelete="CASCADE"), nullable=False, index=True)
    domestica_id = Column(Integer, ForeignKey("domesticas.id", ondelete="CASCADE"), nullable=False, index=True)
    criado_em = Column(DateTime, nullable=False, default=_agora)

    familia = relationship("MpFamiliaDB", back_populates="favoritos")
    domestica = relationship("MpDomesticaDB", back_populates="guardada_por")


class EstadoAcordo:
    PROPOSTO = "proposto"          # a família propôs; falta a doméstica aceitar
    RECUSADO = "recusado"          # a doméstica recusou
    CANCELADO = "cancelado"        # a família desistiu antes de pagar
    ACEITE = "aceite"              # a doméstica aceitou; falta a família pagar
    PAGO = "pago"                  # pago e retido na plataforma até ser libertado
    EM_DISPUTA = "em_disputa"      # retido; a família reportou um problema
    LIBERTADO = "libertado"        # transferido para a doméstica
    REEMBOLSADO = "reembolsado"    # devolvido à família

    # Com dinheiro retido na plataforma: nenhuma das contas pode ser apagada.
    RETIDOS = (PAGO, EM_DISPUTA)


class MpAcordoDB(Base):
    """Serviço combinado entre uma família e uma doméstica, e o pagamento dele.

    A família propõe (o quê, quando, quanto); a doméstica aceita; a família paga e
    o dinheiro fica retido na plataforma; quando a família confirma que o serviço
    foi feito — ou passado o prazo sem ela reportar um problema — o valor menos a
    comissão é transferido para a doméstica."""
    __tablename__ = "acordos"

    id = Column(Integer, primary_key=True)
    gid = Column(String(32), unique=True, nullable=False, index=True, default=_novo_gid)
    # Ficam a NULL se a conta for apagada: o registo do pagamento continua.
    familia_id = Column(Integer, ForeignKey("familias.id", ondelete="SET NULL"), nullable=True, index=True)
    domestica_id = Column(Integer, ForeignKey("domesticas.id", ondelete="SET NULL"), nullable=True, index=True)

    descricao = Column(Text, nullable=False)
    # Contacto que a família escolheu partilhar com a doméstica (a conta não o mostra).
    contacto_familia = Column(String(80), nullable=True)
    data_servico = Column(Date, nullable=False)
    valor_centimos = Column(Integer, nullable=False)
    # Fixada na proposta: mudar a percentagem depois não altera acordos já feitos.
    comissao_centimos = Column(Integer, nullable=False)

    estado = Column(String(16), nullable=False, default=EstadoAcordo.PROPOSTO, index=True)
    motivo_problema = Column(Text, nullable=True)

    stripe_checkout_id = Column(String(255), nullable=True, index=True)
    stripe_pagamento_id = Column(String(64), nullable=True, index=True)
    stripe_cobranca_id = Column(String(64), nullable=True)
    stripe_transferencia_id = Column(String(64), nullable=True)

    criado_em = Column(DateTime, nullable=False, default=_agora)
    atualizado_em = Column(DateTime, nullable=False, default=_agora, onupdate=_agora)
    respondido_em = Column(DateTime, nullable=True)
    pago_em = Column(DateTime, nullable=True)
    # Sem problema reportado, o pagamento segue sozinho para a doméstica neste dia.
    libertar_em = Column(Date, nullable=True, index=True)
    libertado_em = Column(DateTime, nullable=True)
    libertado_automaticamente = Column(Boolean, nullable=False, default=False)

    familia = relationship("MpFamiliaDB", back_populates="acordos")
    domestica = relationship("MpDomesticaDB", back_populates="acordos")

    @property
    def a_receber_centimos(self) -> int:
        return self.valor_centimos - self.comissao_centimos
