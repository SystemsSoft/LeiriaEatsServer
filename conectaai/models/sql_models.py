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
