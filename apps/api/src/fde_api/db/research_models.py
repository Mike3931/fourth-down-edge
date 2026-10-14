"""Append-only current-season research; never overwrite legacy canonical facts."""
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from fde_api.db.models import Base


class ResearchSource(Base):
    __tablename__ = "research_sources"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(32), index=True)
    season: Mapped[int] = mapped_column(index=True)
    provider: Mapped[str] = mapped_column(String(32))
    payload_hash: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str] = mapped_column(String(256))
    published_at: Mapped[datetime | None]
    captured_at: Mapped[datetime]
    coverage: Mapped[dict[str, Any]]


class ResearchFact(Base):
    __tablename__ = "research_facts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("research_sources.id"), index=True)
    team_id: Mapped[str | None] = mapped_column(String(8), index=True)
    player_id: Mapped[str | None] = mapped_column(String(16), index=True)
    week: Mapped[int | None]
    canonical_game_id: Mapped[str | None] = mapped_column(String(32))
    values: Mapped[dict[str, Any]]


class PlayerBinding(Base):
    __tablename__ = "research_player_bindings"
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    player_id: Mapped[str] = mapped_column(String(16), index=True)
    observed_at: Mapped[datetime]
    evidence: Mapped[dict[str, Any]]


class PlayerIdentityReview(Base):
    __tablename__ = "research_player_reviews"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    player_id: Mapped[str | None] = mapped_column(String(16), index=True)
    observed_at: Mapped[datetime]
    reason: Mapped[str] = mapped_column(String(64))
    evidence: Mapped[dict[str, Any]]


class ResearchPoll(Base):
    """Operational attempts; source evidence stays immutable on unchanged polling."""
    __tablename__ = "research_polls"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(32), index=True)
    season: Mapped[int]
    captured_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str | None] = mapped_column(ForeignKey("research_sources.id"))
    error: Mapped[str | None] = mapped_column(String(120))


class GameResearchManifest(Base):
    __tablename__ = "game_research_manifests"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_game_id: Mapped[str] = mapped_column(String(32), index=True)
    cutoff: Mapped[datetime]
    created_at: Mapped[datetime]
    payload: Mapped[dict[str, Any]]
