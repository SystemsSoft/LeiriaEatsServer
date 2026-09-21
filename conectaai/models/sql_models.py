# Arquivo: conectaai/models/sql_models.py
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from conectaai.core.database import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserDB(Base):
    __tablename__ = "users"

    id = Column(String(32), primary_key=True, default=_uuid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False)  # "company" | "creator"
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=_now)

    company = relationship("CompanyDB", back_populates="user", uselist=False, cascade="all, delete-orphan")
    creator = relationship("CreatorDB", back_populates="user", uselist=False, cascade="all, delete-orphan")


class CompanyDB(Base):
    __tablename__ = "companies"

    id = Column(String(32), primary_key=True, default=_uuid)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    segment = Column(String(120), default="")
    city = Column(String(120), default="")
    website = Column(String(255), default="")
    instagram = Column(String(120), default="")
    size = Column(String(50), default="")
    avg_campaign_budget = Column(Float, default=0)
    desired_categories = Column(JSON, default=list)

    user = relationship("UserDB", back_populates="company")


class CreatorDB(Base):
    __tablename__ = "creators"

    id = Column(String(32), primary_key=True, default=_uuid)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    username = Column(String(120), default="")
    city = Column(String(120), default="")
    bio = Column(Text, default="")
    avatar_url = Column(String(500), default="")
    categories = Column(JSON, default=list)
    followers = Column(Integer, default=0)
    engagement_rate = Column(Float, default=0)
    price_min = Column(Float, default=0)
    price_max = Column(Float, default=0)
    platforms = Column(JSON, default=list)
    content_types = Column(JSON, default=list)
    rating = Column(Float, default=0)
    available = Column(Boolean, default=True)
    audience_info = Column(JSON, default=dict)
    portfolio = Column(JSON, default=list)
    followers_history = Column(JSON, default=list)
    engagement_history = Column(JSON, default=list)
    profile_views = Column(Integer, default=0)

    user = relationship("UserDB", back_populates="creator")


class CampaignDB(Base):
    __tablename__ = "campaigns"

    id = Column(String(32), primary_key=True, default=_uuid)
    company_id = Column(String(32), ForeignKey("companies.id"), nullable=False)
    name = Column(String(255), nullable=False)
    budget = Column(Float, default=0)
    status = Column(String(30), default="draft")
    deadline = Column(DateTime, nullable=True)
    description = Column(Text, default="")
    progress = Column(Float, default=0)
    current_stage = Column(String(30), default="briefing")
    content_types = Column(JSON, default=list)

    creator_links = relationship("CampaignCreatorDB", back_populates="campaign", cascade="all, delete-orphan")


class CampaignCreatorDB(Base):
    __tablename__ = "campaign_creators"

    campaign_id = Column(String(32), ForeignKey("campaigns.id"), primary_key=True)
    creator_id = Column(String(32), ForeignKey("creators.id"), primary_key=True)

    campaign = relationship("CampaignDB", back_populates="creator_links")


class ProposalDB(Base):
    __tablename__ = "proposals"

    id = Column(String(32), primary_key=True, default=_uuid)
    company_id = Column(String(32), ForeignKey("companies.id"), nullable=False)
    creator_id = Column(String(32), ForeignKey("creators.id"), nullable=False)
    campaign_id = Column(String(32), ForeignKey("campaigns.id"), nullable=True)
    campaign_name = Column(String(255), default="")
    content_type = Column(String(120), default="")
    quantity = Column(Integer, default=1)
    deadline = Column(DateTime, nullable=True)
    description = Column(Text, default="")
    budget = Column(Float, default=0)
    status = Column(String(30), default="pending")
    message = Column(Text, default="")


class ConversationDB(Base):
    __tablename__ = "conversations"

    id = Column(String(32), primary_key=True, default=_uuid)
    creator_id = Column(String(32), ForeignKey("creators.id"), nullable=False)
    company_id = Column(String(32), ForeignKey("companies.id"), nullable=False)
    campaign_id = Column(String(32), ForeignKey("campaigns.id"), nullable=True)
    campaign_name = Column(String(255), default="")
    status = Column(String(30), default="")
    value = Column(Float, nullable=True)

    messages = relationship(
        "MessageDB", back_populates="conversation", cascade="all, delete-orphan", order_by="MessageDB.timestamp"
    )


class MessageDB(Base):
    __tablename__ = "messages"

    id = Column(String(32), primary_key=True, default=_uuid)
    conversation_id = Column(String(32), ForeignKey("conversations.id"), nullable=False)
    sender_id = Column(String(32), nullable=False)
    text = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=_now)

    conversation = relationship("ConversationDB", back_populates="messages")


class NotificationDB(Base):
    __tablename__ = "notifications"

    id = Column(String(32), primary_key=True, default=_uuid)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False)
    type = Column(String(30), nullable=False)
    title = Column(String(255), default="")
    message = Column(Text, default="")
    timestamp = Column(DateTime, default=_now)
    read = Column(Boolean, default=False)


class OpportunityDB(Base):
    __tablename__ = "opportunities"

    id = Column(String(32), primary_key=True, default=_uuid)
    company_id = Column(String(32), ForeignKey("companies.id"), nullable=False)
    campaign_id = Column(String(32), ForeignKey("campaigns.id"), nullable=True)
    company_name = Column(String(255), default="")
    campaign_name = Column(String(255), default="")
    content_type = Column(String(120), default="")
    category = Column(String(120), default="")
    budget_min = Column(Float, default=0)
    budget_max = Column(Float, default=0)
    deadline = Column(DateTime, nullable=True)


class FavoriteDB(Base):
    __tablename__ = "favorites"

    id = Column(String(32), primary_key=True, default=_uuid)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False)
    target_type = Column(String(20), nullable=False)  # "creator" | "opportunity"
    target_id = Column(String(32), nullable=False)
    created_at = Column(DateTime, default=_now)


class RatingDB(Base):
    """Avaliação de uma empresa sobre um creator — uma por par empresa+creator
    (reenviar substitui a nota anterior). Só pode ser criada depois de uma
    proposta aceita entre os dois (checado na rota, não aqui)."""

    __tablename__ = "ratings"
    __table_args__ = (UniqueConstraint("company_id", "creator_id", name="uq_rating_company_creator"),)

    id = Column(String(32), primary_key=True, default=_uuid)
    company_id = Column(String(32), ForeignKey("companies.id"), nullable=False)
    creator_id = Column(String(32), ForeignKey("creators.id"), nullable=False)
    score = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


# ---------------------------------------------------------------------------
# Negociação entre agentes de IA (v1). Tabelas novas apenas — não alteramos
# nenhuma coluna das tabelas acima porque a criação aqui é só `create_all`
# (sem Alembic): ele cria tabela ausente, mas nunca faz ALTER numa existente.
# ---------------------------------------------------------------------------


class CommercialMandateDB(Base):
    """Um mandato = os limites dentro dos quais o agente de um lado (empresa
    ou creator) pode negociar sozinho. Documento vivo: `active=True` aponta o
    mandato vigente de um `owner_id`; um novo mandato desativa o anterior
    (nunca é editado in-place, para manter histórico auditável)."""

    __tablename__ = "commercial_mandates"

    id = Column(String(32), primary_key=True, default=_uuid)
    owner_type = Column(String(20), nullable=False)  # "company" | "creator"
    # Sem ForeignKey de propósito — guarda company.id OU creator.id, mesmo
    # padrão de MessageDB.sender_id (um id "polimórfico" por owner_type).
    owner_id = Column(String(32), nullable=False, index=True)
    campaign_id = Column(String(32), ForeignKey("campaigns.id"), nullable=True)
    active = Column(Boolean, default=True, index=True)

    objective = Column(String(255), default="")
    target_count = Column(Integer, default=0)
    budget_total = Column(Float, default=0)
    ideal_price = Column(Float, default=0)
    price_floor = Column(Float, default=0)  # usado pelo creator: valor mínimo aceitável
    price_ceiling = Column(Float, default=0)  # usado pela empresa: teto do orçamento
    auto_approve_limit = Column(Float, default=0)  # acima disso, precisa de aprovação humana
    currency = Column(String(3), default="BRL")
    max_rounds = Column(Integer, default=4)

    deliverables = Column(JSON, default=list)  # [{"content_type": "Reel", "min_qty": 1, "max_qty": 3}, ...]
    negotiable_fields = Column(JSON, default=list)  # ["price", "deliverables", "deadline", ...]
    non_negotiable_fields = Column(JSON, default=list)  # ["exclusivity", "usage_rights"]
    deadline_earliest = Column(DateTime, nullable=True)
    deadline_latest = Column(DateTime, nullable=True)
    exclusivity_allowed = Column(Boolean, default=False)
    extra_terms = Column(JSON, default=dict)

    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class NegotiationDB(Base):
    """Uma negociação entre o agente da empresa e o agente do creator (ou,
    sem mandato do creator, o fluxo cai para conversa manual — ver
    `creator_mandate_id` nullable). `state` é a máquina de estados de
    `services/negotiation/state_machine.py`."""

    __tablename__ = "negotiations"

    id = Column(String(32), primary_key=True, default=_uuid)
    company_id = Column(String(32), ForeignKey("companies.id"), nullable=False)
    creator_id = Column(String(32), ForeignKey("creators.id"), nullable=False)
    campaign_id = Column(String(32), ForeignKey("campaigns.id"), nullable=True)
    conversation_id = Column(String(32), ForeignKey("conversations.id"), nullable=True)

    company_mandate_id = Column(String(32), ForeignKey("commercial_mandates.id"), nullable=False)
    creator_mandate_id = Column(String(32), ForeignKey("commercial_mandates.id"), nullable=True)

    state = Column(String(30), default="draft", index=True)
    round_no = Column(Integer, default=0)
    max_rounds = Column(Integer, default=4)
    current_offer = Column(JSON, default=dict)
    last_actor = Column(String(20), default="")
    outcome = Column(String(30), default="")
    outcome_reason = Column(Text, default="")
    deadline_at = Column(DateTime, nullable=True)

    # Controle de concorrência do runner em background (ver services/negotiation/runner.py):
    # só um worker por vez processa uma negociação `running`.
    lease_owner = Column(String(64), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    error_count = Column(Integer, default=0)

    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    turns = relationship(
        "NegotiationTurnDB", back_populates="negotiation", cascade="all, delete-orphan", order_by="NegotiationTurnDB.created_at"
    )
    agreement = relationship("AgreementDB", back_populates="negotiation", uselist=False, cascade="all, delete-orphan")


class NegotiationTurnDB(Base):
    """Um turno = uma rodada de um dos agentes. Guarda os 3 estágios do dado
    para auditoria: o que o LLM propôs (`proposed_terms`), o que sobrou depois
    do mandato aplicado (`terms_after_policy`) e o que foi cortado
    (`policy_violations`) — assim dá pra responder "por que o agente aceitou
    R$ X" sem adivinhar."""

    __tablename__ = "negotiation_turns"
    __table_args__ = (UniqueConstraint("negotiation_id", "round_no", "actor", name="uq_turn_negotiation_round_actor"),)

    id = Column(String(32), primary_key=True, default=_uuid)
    negotiation_id = Column(String(32), ForeignKey("negotiations.id"), nullable=False, index=True)
    round_no = Column(Integer, nullable=False)
    actor = Column(String(20), nullable=False)  # company_agent | creator_agent | human_company | human_creator | system
    intent = Column(String(20), default="")  # offer | counter_offer | accept | reject | escalate

    proposed_terms = Column(JSON, default=dict)
    terms_after_policy = Column(JSON, default=dict)
    policy_violations = Column(JSON, default=list)
    rationale = Column(Text, default="")  # explicação curta do LLM (ou do agente determinístico) — só para auditoria

    message_text = Column(Text, default="")
    message_id = Column(String(32), nullable=True)  # aponta messages.id, quando espelhado na Conversation
    ai_call_id = Column(String(32), nullable=True)  # aponta ai_call_logs.id, quando gerado por LLM

    created_at = Column(DateTime, default=_now)

    negotiation = relationship("NegotiationDB", back_populates="turns")


class AgreementDB(Base):
    """O acordo estruturado ao final de uma negociação bem-sucedida — uma
    minuta, não um contrato: fica em `awaiting_approval` até os dois lados
    aprovarem, e só então vira uma `ProposalDB` (status="accepted")."""

    __tablename__ = "agreements"

    id = Column(String(32), primary_key=True, default=_uuid)
    negotiation_id = Column(String(32), ForeignKey("negotiations.id"), nullable=False, unique=True)
    company_id = Column(String(32), ForeignKey("companies.id"), nullable=False)
    creator_id = Column(String(32), ForeignKey("creators.id"), nullable=False)
    campaign_id = Column(String(32), ForeignKey("campaigns.id"), nullable=True)

    terms = Column(JSON, default=dict)  # {"price": 180, "deliverables": [...], "deadline_days": 10, ...}
    total_value = Column(Float, default=0)
    status = Column(String(30), default="awaiting_approval")  # awaiting_approval | approved | rejected | expired

    company_approved_at = Column(DateTime, nullable=True)
    creator_approved_at = Column(DateTime, nullable=True)
    rejected_by = Column(String(20), default="")
    reject_reason = Column(Text, default="")
    proposal_id = Column(String(32), nullable=True)  # aponta proposals.id, criado quando os dois aprovam

    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    negotiation = relationship("NegotiationDB", back_populates="agreement")


class EntityEmbeddingDB(Base):
    """Vetor de embedding (Gemini, via API — nenhum modelo local nesse
    processo) de um creator ou campanha, para matching semântico. Sem
    pgvector: a similaridade é calculada em Python (ver
    services/matching_service.py) sobre os vetores carregados do MySQL."""

    __tablename__ = "entity_embeddings"
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", "model", name="uq_embedding_entity_model"),)

    id = Column(String(32), primary_key=True, default=_uuid)
    entity_type = Column(String(20), nullable=False)  # "creator" | "campaign"
    entity_id = Column(String(32), nullable=False)
    model = Column(String(60), nullable=False)
    dim = Column(Integer, nullable=False)
    vector = Column(LargeBinary, nullable=False)  # float32 little-endian, já normalizado (L2)
    source_hash = Column(String(64), nullable=False)  # sha256 do texto fonte — evita regerar sem necessidade

    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class AiCallLogDB(Base):
    """Trilha de auditoria de toda chamada ao Gemini feita pelo módulo de
    negociação — nunca loga o prompt inteiro (LGPD: dado de perfil de
    terceiro), só o hash e um resumo truncado da resposta."""

    __tablename__ = "ai_call_logs"

    id = Column(String(32), primary_key=True, default=_uuid)
    negotiation_id = Column(String(32), ForeignKey("negotiations.id"), nullable=True, index=True)
    turn_id = Column(String(32), nullable=True)
    purpose = Column(String(40), default="")  # negotiation_turn | extract_terms | campaign_draft | embedding
    model = Column(String(60), default="")
    key_index = Column(Integer, default=0)
    attempt = Column(Integer, default=1)
    status = Column(String(20), default="ok")  # ok | error | timeout | invalid_json
    latency_ms = Column(Integer, default=0)
    prompt_hash = Column(String(64), default="")
    prompt_chars = Column(Integer, default=0)
    response_raw = Column(Text, default="")  # truncado a 8000 chars em AiCallLogRepository.create
    error = Column(Text, default="")

    created_at = Column(DateTime, default=_now, index=True)
