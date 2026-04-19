"""
Detection module — database models.

Two tables:
  llm_token_baseline  — per-tenant statistical profile, refreshed nightly.
                        Stores the stats ALL three detectors need.
  llm_anomaly         — every detected anomaly, with which detector(s) fired.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float,
    Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class LLMTokenBaseline(Base):
    """
    Statistical profile for a tenant's daily usage.

    Populated by DetectionService.compute_baseline_for_tenant().
    One row per tenant, upserted on each computation.

    Stores everything the three detectors need:
    - mean/std_dev for Z-score fallback
    - last 30 daily values (JSON array) for STL and IsolationForest
    - CUSUM state (cumulative sum and reference level)
    """

    __tablename__ = "llm_token_baseline"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(100), nullable=False, default="*")
    model: Mapped[str] = mapped_column(String(100), nullable=False, default="*")

    # --- Classical stats (used for Z-score fallback and context) ---
    daily_mean: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    daily_std_dev: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    daily_min: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    daily_max: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    call_count_mean: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    call_count_std_dev: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    daily_cost_mean: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    daily_cost_std_dev: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error_rate_mean: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # --- CUSUM state (persisted between checks) ---
    # cusum_pos: cumulative sum of above-mean deviations
    # cusum_neg: cumulative sum of below-mean deviations
    # cusum_k:   reference level (slack parameter) = 0.5 * std_dev
    cusum_pos: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cusum_neg: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cusum_k: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # IsolationForest contamination estimate from training
    isolation_forest_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, default=-0.1
    )

    # How many days of data was this baseline computed from
    sample_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    baseline_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    baseline_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "model", name="uq_baseline_scope"),
    )


class LLMAnomalyRecord(Base):
    """
    A detected anomaly.

    detector_votes: comma-separated list of detectors that fired,
    e.g. "stl,isolation_forest" or "cusum" or "stl,cusum,isolation_forest"

    The severity is determined by how many detectors voted AND
    the magnitude of the most extreme score.

    severity: normal / warning / high / critical
    anomaly_type: token_spike / token_drop / cost_spike /
                  error_rate_spike / pattern_break
    """

    __tablename__ = "llm_anomaly"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(100), nullable=False, default="*")
    model: Mapped[str] = mapped_column(String(100), nullable=False, default="*")

    anomaly_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)

    # Which ML detectors voted for this anomaly
    detector_votes: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # How many of the 3 detectors voted (1, 2, or 3)
    vote_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Scores from each detector (0 = not fired, otherwise the score)
    stl_residual_zscore: Mapped[float] = mapped_column(Float, nullable=True)
    isolation_score: Mapped[float] = mapped_column(Float, nullable=True)
    cusum_value: Mapped[float] = mapped_column(Float, nullable=True)

    # Context
    observed_value: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_mean: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_std_dev: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)

    trigger_log_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    __table_args__ = (
        Index("ix_anomaly_tenant_detected", "tenant_id", "detected_at"),
        Index("ix_anomaly_severity", "severity"),
    )