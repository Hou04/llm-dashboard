import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    String,
    Integer,
    BigInteger,
    Float,
    Numeric,
    Boolean,
    DateTime,
    Text,
    ForeignKey,
    Index,
    UniqueConstraint,
    PrimaryKeyConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base


# ============================================================
# ENUMS
#
# Python enums used for columns with fixed allowed values.
# Using enums means invalid values are caught at the Python
# layer before they ever reach the database.
# ============================================================

class CallStatus(str, PyEnum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"


class GovernanceDecision(str, PyEnum):
    ALLOW = "allow"
    ALLOW_DOWNGRADE = "allow_downgrade"
    ALLOW_FALLBACK = "allow_fallback"
    BLOCK = "block"


class RuleType(str, PyEnum):
    TENANT_LIMIT = "tenant_limit"
    MODEL_BLOCK = "model_block"
    BUDGET_CAP = "budget_cap"
    RATE_LIMIT = "rate_limit"
    MODEL_DOWNGRADE = "model_downgrade"

class ProviderStatus(str, PyEnum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"


# ============================================================
# TABLE 1: LLMTokenLog
#
# The core time-series table. One row per LLM API call.
# This is the source of truth for all analytics and billing.
#
# Made a TimescaleDB hypertable during migration — Alembic
# calls SELECT create_hypertable(...) after creating the table.
# Partitioned by created_at in 7-day chunks.
# ============================================================

class LLMTokenLog(Base):
    __tablename__ = "llm_token_log"

    # ---- Identity ----
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        nullable=False,
        comment="Unique call identifier",
    )
    request_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="External request ID from the API caller for correlation",
    )
    trace_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment="Gateway-generated trace ID for end-to-end correlation across M1→M10",
    )

    # ---- Who made the call ----
    tenant_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Tenant that made this call",
    )
    agent_id: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="Agent or service within the tenant that made the call",
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="End user ID if available",
    )
    site_id: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="Physical or logical site identifier",
    )
    module: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="Application module generating the call",
    )
    pricing_profile: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Pricing tier or contract profile applied",
    )

    # ---- What was called ----
    model: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="LLM model name e.g. gpt-4o-mini, claude-3-haiku",
    )
    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="LLM provider e.g. openai, anthropic, google",
    )

    # ---- Token consumption ----
    input_tokens: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        comment="Number of tokens in the prompt/input",
    )
    output_tokens: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        comment="Number of tokens in the completion/output",
    )
    total_tokens: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        comment="Total tokens = input + output",
    )

    # ---- Cost ----
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=8),
        nullable=False,
        default=Decimal("0"),
        comment="Cost in USD calculated at call time",
    )

    # ---- Performance ----
    duration_ms: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="End-to-end call duration in milliseconds",
    )

    # ---- Status ----
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CallStatus.SUCCESS.value,
        server_default=CallStatus.SUCCESS.value,
        index=True,
        comment="Call outcome: success, error, timeout, blocked",
    )

    def __init__(self, **kwargs):
        kwargs.setdefault("status", CallStatus.SUCCESS.value)
        kwargs.setdefault("input_tokens", 0)
        kwargs.setdefault("output_tokens", 0)
        kwargs.setdefault("total_tokens", 0)
        kwargs.setdefault("cost_usd", Decimal("0"))
        super().__init__(**kwargs)
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if status is error or timeout",
    )
    quality_score: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment="Response quality score 0.0-1.0, populated by downstream evaluation. Used to train the M7 smart router.",
    )

    # ---- Prompt Inspector (Feature 2) ----
    prompt_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="The prompt/input text sent to the LLM. Stored for the Request Inspector.",
    )
    completion_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="The completion/response text from the LLM. Stored for the Request Inspector.",
    )
    pii_redacted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="True if PII was detected and redacted in prompt/completion text.",
    )

    # ---- Metadata ----
    metadata_: Mapped[Optional[dict]] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        comment="Additional call metadata as JSON",
    )

    # ---- Time ----
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
        comment="UTC timestamp of the call — TimescaleDB partition key",
    )

    # ---- Indexes for common query patterns ----
    __table_args__ = (
        # TimescaleDB requires the partition column (created_at)
        # to be part of every unique index including the primary key.
        PrimaryKeyConstraint("id", "created_at"),
        Index(
            "ix_llm_token_log_tenant_created",
            "tenant_id", "created_at",
        ),
        Index(
            "ix_llm_token_log_model_created",
            "model", "created_at",
        ),
        Index(
            "ix_llm_token_log_tenant_agent",
            "tenant_id", "agent_id",
        ),
        {
            "comment": "Core time-series table for all LLM API calls. "
                       "Made a TimescaleDB hypertable during migration."
        },
    )
    def __repr__(self) -> str:
        return (
            f"<LLMTokenLog id={self.id} tenant={self.tenant_id} "
            f"model={self.model} tokens={self.total_tokens}>"
        )

    @property
    def is_successful(self) -> bool:
        return self.status == CallStatus.SUCCESS.value

    @property
    def cost_float(self) -> float:
        return float(self.cost_usd)


# ============================================================
# TABLE 2: LLMGovernanceRule
#
# Stores the rules that control which tenants can use
# which models and at what limits.
# Small table, read-heavy, heavily cached in Redis.
# ============================================================

class LLMGovernanceRule(Base):
    __tablename__ = "llm_governance_rules"

    # ---- Identity ----
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Rule identifier",
    )

    # ---- Rule definition ----
    rule_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        comment="Type of rule: tenant_limit, model_block, budget_cap, etc.",
    )
    tenant_id: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="Target tenant. NULL means rule applies to all tenants.",
    )
    model_name: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Target model. NULL means rule applies to all models.",
    )

    # ---- Limit values ----
    daily_token_limit: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Maximum tokens allowed per day. NULL means no limit.",
    )
    monthly_token_limit: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Maximum tokens allowed per month. NULL means no limit.",
    )
    monthly_budget_usd: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(precision=10, scale=2),
        nullable=True,
        comment="Maximum spend in USD per month. NULL means no limit.",
    )
    max_tokens_per_request: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Maximum tokens allowed in a single request.",
    )

    # ---- Rule behaviour ----
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        comment="Higher priority rules win when multiple rules match. Default 100.",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
        comment="Inactive rules are ignored without being deleted.",
    )
    downgrade_to_model: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="For MODEL_DOWNGRADE rules: substitute this model instead.",
    )

    # ---- Time bounds ----
    effective_from: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Rule is inactive before this UTC datetime.",
    )
    effective_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Rule is inactive after this UTC datetime.",
    )

    # ---- Audit ----
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Human-readable explanation of what this rule does.",
    )
    created_by: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Admin user who created this rule.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        comment="UTC timestamp when rule was created.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="UTC timestamp when rule was last updated.",
    )

    # ---- Relationships ----
    decisions: Mapped[list["LLMGovernanceDecision"]] = relationship(
        "LLMGovernanceDecision",
        back_populates="rule",
        lazy="dynamic",
    )

    __table_args__ = (
        Index(
            "ix_governance_rules_tenant_active",
            "tenant_id", "is_active",
        ),
        Index(
            "ix_governance_rules_type_active",
            "rule_type", "is_active",
        ),
        {
            "comment": "Governance rules controlling tenant access and limits."
        },
    )

    def __repr__(self) -> str:
        return (
            f"<LLMGovernanceRule id={self.id} type={self.rule_type} "
            f"tenant={self.tenant_id} active={self.is_active}>"
        )

    @property
    def applies_to_all_tenants(self) -> bool:
        return self.tenant_id is None

    @property
    def applies_to_all_models(self) -> bool:
        return self.model_name is None


# ============================================================
# TABLE 3: LLMGovernanceDecision
#
# Audit log of every governance decision made.
# Links every allowed/blocked call to the rule that decided it.
# ============================================================

class LLMGovernanceDecision(Base):
    __tablename__ = "llm_governance_decisions"

    # ---- Identity ----
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Decision identifier",
    )

    # ---- What was decided ----
    request_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment="Correlates with llm_token_log.request_id",
    )
    tenant_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Tenant whose call was evaluated",
    )
    model_requested: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Model the tenant requested",
    )
    model_used: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Model actually used (differs from requested on ALLOW_DOWNGRADE)",
    )

    # ---- The decision ----
    decision: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="Outcome: allow, allow_downgrade, block",
    )
    reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Human-readable reason for the decision",
    )

    # ---- Which rule triggered it ----
    rule_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_governance_rules.id", ondelete="SET NULL"),
        nullable=True,
        comment="Rule that triggered this decision. NULL if no rule matched.",
    )

    # ---- Usage at time of decision ----
    tokens_used_today: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Tenant token usage at time of decision (from Redis)",
    )
    budget_used_today: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(precision=10, scale=4),
        nullable=True,
        comment="Tenant cost at time of decision (from Redis)",
    )

    # ---- Time ----
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
        comment="UTC timestamp when governance was evaluated",
    )

    # ---- Relationship ----
    rule: Mapped[Optional["LLMGovernanceRule"]] = relationship(
        "LLMGovernanceRule",
        back_populates="decisions",
    )

    __table_args__ = (
        Index(
            "ix_governance_decisions_tenant_evaluated",
            "tenant_id", "evaluated_at",
        ),
        Index(
            "ix_governance_decisions_decision_evaluated",
            "decision", "evaluated_at",
        ),
        {
            "comment": "Audit log of all governance decisions."
        },
    )

    def __repr__(self) -> str:
        return (
            f"<LLMGovernanceDecision id={self.id} "
            f"tenant={self.tenant_id} decision={self.decision}>"
        )

    @property
    def was_blocked(self) -> bool:
        return self.decision == GovernanceDecision.BLOCK.value

    @property
    def was_downgraded(self) -> bool:
        return self.decision == GovernanceDecision.ALLOW_DOWNGRADE.value

# ============================================================
# TABLE 4: LLMProviderStatus (Health Check)
# ============================================================

class LLMProviderStatus(Base):
    """
    Real-time health and latency tracking for AI Providers.
    Updated by background heartbeat workers.
    """
    __tablename__ = "llm_provider_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="online")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    uptime_pct: Mapped[float] = mapped_column(Float, default=100.0)
    last_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<LLMProviderStatus {self.provider_name} status={self.status} latency={self.latency_ms}ms>"