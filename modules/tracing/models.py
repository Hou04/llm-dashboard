"""
Session Tracing & Prompt A/B Testing — SQLAlchemy Models.

Feature 8: LLMSession — groups multiple LLM calls into a single trace/session.
Feature 9: LLMExperiment — A/B testing between two prompt versions.
Feature 10: LLMAlertConfig — per-tenant webhook configuration for alerting.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    Text,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


# ============================================================
# FEATURE 8: SESSION TRACING
# ============================================================

class SessionStatus(str, PyEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class LLMSession(Base):
    """
    Groups multiple LLM calls into a single trace/session.

    A session represents one user interaction or agent chain that may
    involve multiple LLM calls (e.g., RAG → summarize → format).
    The client provides a session_id via header or request body;
    the gateway links each log entry to this session.

    Usage:
      X-Session-Id: sess_abc123   (header on each call)
      → All calls with the same session_id are grouped
      → Dashboard shows a waterfall view with latency and cost per step
    """

    __tablename__ = "llm_sessions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True,
        comment="Client-provided session identifier for grouping calls",
    )
    tenant_id: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True,
        comment="User who initiated this session",
    )
    agent_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True,
        comment="Agent/pipeline that owns this session",
    )
    name: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True,
        comment="Human-readable session name, e.g. 'Report Generation Pipeline'",
    )

    # ---- Aggregated metrics (updated on each call) ----
    total_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Total number of LLM calls in this session",
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    total_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    total_output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    total_cost_usd: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0,
    )
    total_duration_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    # ---- Lifecycle ----
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SessionStatus.ACTIVE.value,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # ---- Metadata ----
    metadata_: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSONB, nullable=True,
        comment="Arbitrary session metadata",
    )
    tags: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True,
    )

    # ---- Call chain (ordered list of log IDs) ----
    call_chain: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, default=list,
        comment="Ordered list of LLMTokenLog IDs in this session",
    )

    __table_args__ = (
        Index("ix_session_tenant", "tenant_id"),
        Index("ix_session_started", "started_at"),
        Index("ix_session_agent", "tenant_id", "agent_id"),
    )

    def __repr__(self) -> str:
        return f"<LLMSession {self.session_id} calls={self.total_calls} ${self.total_cost_usd:.4f}>"


# ============================================================
# FEATURE 9: PROMPT A/B TESTING
# ============================================================

class ExperimentStatus(str, PyEnum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class LLMExperiment(Base):
    """
    A/B test between two prompt versions on live traffic.

    Routes a percentage of traffic to variant B while the rest
    goes to variant A (control). Tracks per-variant metrics:
    latency, cost, quality_score, error_rate.

    When statistical significance is reached, a "winner" is declared.
    """

    __tablename__ = "llm_experiments"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(
        String(200), nullable=False,
        comment="Experiment name, e.g. 'Shorter summarization prompt'",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
    )

    # ---- Variants ----
    prompt_name: Mapped[str] = mapped_column(
        String(200), nullable=False,
        comment="Prompt template name being tested",
    )
    variant_a_version: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="Prompt version for control (A)",
    )
    variant_b_version: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="Prompt version for treatment (B)",
    )
    traffic_split: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5,
        comment="Fraction of traffic routed to variant B (0.0-1.0)",
    )

    # ---- Primary metric ----
    primary_metric: Mapped[str] = mapped_column(
        String(50), nullable=False, default="quality_score",
        comment="Metric to compare: quality_score | latency | cost | error_rate",
    )

    # ---- Variant A metrics ----
    a_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    a_avg_latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    a_avg_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    a_avg_quality: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    a_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    a_total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ---- Variant B metrics ----
    b_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    b_avg_latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    b_avg_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    b_avg_quality: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    b_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    b_total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ---- Results ----
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ExperimentStatus.DRAFT.value,
    )
    winner: Mapped[Optional[str]] = mapped_column(
        String(1), nullable=True,
        comment="'A' or 'B' — set when significance is reached",
    )
    p_value: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True,
        comment="Statistical p-value from the comparison test",
    )
    confidence_level: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True,
        comment="Confidence level (e.g., 0.95)",
    )
    min_samples: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100,
        comment="Minimum samples per variant before declaring a winner",
    )

    # ---- Lifecycle ----
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        Index("ix_experiment_tenant", "tenant_id"),
        Index("ix_experiment_prompt", "tenant_id", "prompt_name"),
        Index("ix_experiment_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<LLMExperiment {self.name} A=v{self.variant_a_version} B=v{self.variant_b_version} [{self.status}]>"

    @property
    def a_error_rate(self) -> float:
        return self.a_error_count / max(self.a_requests, 1)

    @property
    def b_error_rate(self) -> float:
        return self.b_error_count / max(self.b_requests, 1)

    def select_variant(self) -> str:
        """Select A or B based on traffic split. Returns 'A' or 'B'."""
        import random
        return "B" if random.random() < self.traffic_split else "A"


# ============================================================
# FEATURE 10: ALERT CONFIGURATION (per-tenant)
# ============================================================

class AlertChannelType(str, PyEnum):
    SLACK = "slack"
    TEAMS = "teams"
    PAGERDUTY = "pagerduty"
    GENERIC = "generic"
    EMAIL = "email"


class LLMAlertConfig(Base):
    """
    Per-tenant alert webhook configuration.

    Replaces the global ALERT_WEBHOOKS env var with a tenant-scoped,
    UI-manageable configuration. Each tenant can have multiple
    alert channels (Slack, Teams, PagerDuty, etc.).
    """

    __tablename__ = "llm_alert_configs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False,
        comment="Channel name, e.g. '#ops-alerts' or 'PagerDuty Critical'",
    )
    channel_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="slack | teams | pagerduty | generic | email",
    )
    webhook_url: Mapped[str] = mapped_column(
        String(500), nullable=False,
        comment="Webhook URL to POST alerts to",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )

    # ---- Filtering ----
    alert_types: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, default=list,
        comment="Which alert types to receive: ['blocked', 'budget_exceeded', 'anomaly', 'fallback']",
    )
    severity_filter: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True,
        comment="Minimum severity: info | warning | error | critical",
    )

    # ---- Metadata ----
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_tested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_alert_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    total_alerts_sent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    __table_args__ = (
        Index("ix_alert_config_tenant", "tenant_id"),
    )


class LLMAlertHistory(Base):
    """
    Log of all dispatched alerts for audit trail.
    """

    __tablename__ = "llm_alert_history"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
    )
    config_id: Mapped[str] = mapped_column(
        String(36), nullable=False,
        comment="FK to llm_alert_configs",
    )
    channel_type: Mapped[str] = mapped_column(String(20), nullable=False)
    alert_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="blocked | budget_exceeded | anomaly | fallback | test",
    )
    payload: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True,
        comment="The payload sent to the webhook (sensitive fields redacted)",
    )
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_alert_history_tenant", "tenant_id"),
        Index("ix_alert_history_sent", "sent_at"),
    )
