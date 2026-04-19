"""
M5 Optimizer — database models.

Two tables per the architecture spec:
  llm_prompt_optimization    — structural recommendations per agent/use-case
  llm_model_recommendation   — model substitution recommendations per agent

Design principles:
- Recommendations are proposed first, then validated/deployed
- Every recommendation includes ROI simulation (tokens/cost before vs after)
- All changes are versioned and traceable
- No raw prompts stored — analysis is based on statistics only
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean, DateTime, Float, Integer,
    Numeric, String, Text, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from core.database import Base


class LLMPromptOptimization(Base):
    """
    Structural optimization recommendation for one agent/use-case.

    Based on observed token usage patterns — never on raw prompt content.

    optimization_type: what kind of structural issue was identified
        verbose_system_prompt    — system prompt is longer than needed
        redundant_context        — context is repeated across calls
        missing_output_format    — no structured output format imposed
        excessive_history        — too much conversation history included
        model_mismatch           — using expensive model for simple task

    status lifecycle: proposed → validated → deployed / rejected
    """

    __tablename__ = "llm_prompt_optimization"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(100), nullable=False)
    use_case: Mapped[str] = mapped_column(
        String(100), nullable=False, default="general"
    )

    optimization_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # What was observed (statistics, not raw content)
    observed_avg_input_tokens: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    observed_avg_output_tokens: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    observed_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The recommendation
    recommendation_title: Mapped[str] = mapped_column(String(200), nullable=False)
    recommendation_detail: Mapped[str] = mapped_column(Text, nullable=True)

    # ROI simulation
    estimated_token_reduction_pct: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    expected_token_saving_monthly: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    expected_cost_saving_monthly_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=10, scale=4), nullable=False, default=Decimal("0")
    )

    # Lifecycle
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="proposed"
    )
    # proposed / validated / deployed / rejected

    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # 1 = highest impact, 5 = lowest

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    
    embedding: Mapped[list[float]] = mapped_column(
        Vector(384), nullable=True
    )

    __table_args__ = (
        Index("ix_prompt_opt_tenant_agent", "tenant_id", "agent_id"),
        Index("ix_prompt_opt_status", "status"),
    )


class LLMModelRecommendation(Base):
    """
    Model substitution recommendation for one agent.

    When M5 determines that an agent is using a more expensive model
    than its actual complexity profile requires, it generates a
    model recommendation with ROI simulation.

    Justification categories:
        low_complexity    — avg tokens and task complexity suggest lighter model
        high_error_rate   — current model may be too weak, upgrade justified
        cost_optimization — same quality achievable at lower cost
        capability_gap    — current model insufficient for task complexity

    One recommendation per (tenant_id, agent_id) — upserted on recompute.
    """

    __tablename__ = "llm_model_recommendation"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(100), nullable=False)

    current_model: Mapped[str] = mapped_column(String(100), nullable=False)
    recommended_model: Mapped[str] = mapped_column(String(100), nullable=False)

    justification_type: Mapped[str] = mapped_column(String(30), nullable=False)
    justification_text: Mapped[str] = mapped_column(Text, nullable=True)

    # Profile that drove the recommendation
    avg_input_tokens: Mapped[float] = mapped_column(Float, nullable=False)
    avg_output_tokens: Mapped[float] = mapped_column(Float, nullable=False)
    avg_cost_per_call: Mapped[float] = mapped_column(Float, nullable=False)
    error_rate_pct: Mapped[float] = mapped_column(Float, nullable=False)
    monthly_call_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # ROI simulation
    current_monthly_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=10, scale=4), nullable=False
    )
    projected_monthly_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=10, scale=4), nullable=False
    )
    expected_monthly_saving_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=10, scale=4), nullable=False
    )
    expected_saving_pct: Mapped[float] = mapped_column(Float, nullable=False)

    # Lifecycle
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="proposed"
    )
    confidence: Mapped[str] = mapped_column(
        String(20), nullable=False, default="medium"
    )
    # low / medium / high

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "agent_id",
            name="uq_model_rec_tenant_agent"
        ),
        Index("ix_model_rec_tenant", "tenant_id"),
    )

class LLMPromptVersion(Base):
    """
    Versioning history for prompt descriptions and rewrites.
    Tracks structural optimizations to allow rolling back if output quality drops.
    """
    __tablename__ = "llm_prompt_version"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(100), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        nullable=False, 
        default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_prompt_ver_tenant_agent", "tenant_id", "agent_id"),
    )