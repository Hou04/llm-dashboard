"""OptimizerRepository — reads agent usage stats and writes optimization records."""

import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, delete, text, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from modules.forecasting.models_optimizer import (
    LLMPromptOptimization,
    LLMModelRecommendation,
    LLMPromptVersion,
)


class OptimizerRepository:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_agent_profiles(
        self, tenant_id: str, lookback_days: int = 30
    ) -> list[dict]:
        """
        Compute per-agent usage profile from llm_token_log.

        Returns one dict per agent with:
            agent_id, model, call_count, avg_input_tokens,
            avg_output_tokens, avg_total_tokens, avg_cost_per_call,
            error_rate_pct, total_cost_usd

        This is the statistical foundation M5 uses — no raw prompts,
        only aggregated metrics per agent.
        """
        from_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        result = await self.session.execute(
            text("""
                SELECT
                    agent_id,
                    model,
                    COUNT(*)                                        AS call_count,
                    AVG(input_tokens)                              AS avg_input_tokens,
                    AVG(output_tokens)                             AS avg_output_tokens,
                    AVG(total_tokens)                              AS avg_total_tokens,
                    AVG(cost_usd)                                  AS avg_cost_per_call,
                    COUNT(*) FILTER (WHERE status = 'error')::FLOAT
                        / NULLIF(COUNT(*), 0) * 100                AS error_rate_pct,
                    SUM(cost_usd)                                  AS total_cost_usd,
                    SUM(total_tokens)                              AS total_tokens
                FROM llm_token_log
                WHERE
                    tenant_id = :tenant_id
                    AND created_at >= :from_dt
                    AND status != 'blocked'
                    AND agent_id IS NOT NULL
                GROUP BY agent_id, model
                ORDER BY SUM(cost_usd) DESC
            """),
            {"tenant_id": tenant_id, "from_dt": from_dt},
        )
        rows = result.fetchall()
        return [
            {
                "agent_id": row.agent_id,
                "model": row.model,
                "call_count": int(row.call_count),
                "avg_input_tokens": float(row.avg_input_tokens or 0),
                "avg_output_tokens": float(row.avg_output_tokens or 0),
                "avg_total_tokens": float(row.avg_total_tokens or 0),
                "avg_cost_per_call": float(row.avg_cost_per_call or 0),
                "error_rate_pct": float(row.error_rate_pct or 0),
                "total_cost_usd": float(row.total_cost_usd or 0),
                "total_tokens": int(row.total_tokens or 0),
            }
            for row in rows
        ]

    async def upsert_model_recommendation(
        self, tenant_id: str, rec: dict
    ) -> LLMModelRecommendation:
        """Insert or update a model recommendation (one per agent)."""
        stmt = (
            pg_insert(LLMModelRecommendation)
            .values(id=uuid.uuid4(), tenant_id=tenant_id, **rec)
            .on_conflict_do_update(
                constraint="uq_model_rec_tenant_agent",
                set_={k: rec[k] for k in rec if k != "agent_id"},
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

        result = await self.session.execute(
            select(LLMModelRecommendation).where(
                and_(
                    LLMModelRecommendation.tenant_id == tenant_id,
                    LLMModelRecommendation.agent_id == rec["agent_id"],
                )
            )
        )
        return result.scalar_one()

    async def create_prompt_optimization(
        self, rec: dict
    ) -> LLMPromptOptimization:
        """Insert a new prompt optimization recommendation."""
        opt = LLMPromptOptimization(**rec)
        self.session.add(opt)
        await self.session.flush()
        await self.session.refresh(opt)
        return opt

    async def get_model_recommendations(
        self, tenant_id: str
    ) -> list[LLMModelRecommendation]:
        result = await self.session.execute(
            select(LLMModelRecommendation)
            .where(LLMModelRecommendation.tenant_id == tenant_id)
            .order_by(LLMModelRecommendation.expected_monthly_saving_usd.desc())
        )
        return list(result.scalars().all())

    async def get_all_model_recommendations(
        self, status: Optional[str] = None
    ) -> list[LLMModelRecommendation]:
        conditions = []
        if status:
            conditions.append(LLMModelRecommendation.status == status)
        result = await self.session.execute(
            select(LLMModelRecommendation)
            .where(*conditions)
            .order_by(LLMModelRecommendation.expected_monthly_saving_usd.desc())
        )
        return list(result.scalars().all())

    async def get_prompt_optimizations(
        self, tenant_id: str, status: Optional[str] = None
    ) -> list[LLMPromptOptimization]:
        conditions = [LLMPromptOptimization.tenant_id == tenant_id]
        if status:
            conditions.append(LLMPromptOptimization.status == status)
        result = await self.session.execute(
            select(LLMPromptOptimization)
            .where(and_(*conditions))
            .order_by(LLMPromptOptimization.priority.asc())
        )
        return list(result.scalars().all())

    async def update_recommendation_status(
        self,
        recommendation_id: uuid.UUID,
        model: str,
        status: str,
    ) -> bool:
        """Update status of a recommendation. model: 'prompt' or 'model'."""
        now = datetime.now(timezone.utc)
        if model == "prompt":
            rec = await self.session.get(LLMPromptOptimization, recommendation_id)
        else:
            rec = await self.session.get(LLMModelRecommendation, recommendation_id)

        if rec is None:
            return False
        rec.status = status
        if hasattr(rec, "updated_at"):
            rec.updated_at = now
        await self.session.flush()
        return True

    # ================================================================
    # PROMPT VERSIONING
    # ================================================================

    async def create_prompt_version(self, tenant_id: str, agent_id: str, prompt_template: str) -> LLMPromptVersion:
        # Deactivate previous active version
        await self.session.execute(
            text("UPDATE llm_prompt_version SET is_active = FALSE WHERE tenant_id = :tid AND agent_id = :aid"),
            {"tid": tenant_id, "aid": agent_id}
        )
        
        # Get next version number
        result = await self.session.execute(
            select(LLMPromptVersion.version_number)
            .where(LLMPromptVersion.tenant_id == tenant_id, LLMPromptVersion.agent_id == agent_id)
            .order_by(LLMPromptVersion.version_number.desc())
            .limit(1)
        )
        last_ver = result.scalar()
        next_ver = (last_ver or 0) + 1
        
        new_version = LLMPromptVersion(
            tenant_id=tenant_id,
            agent_id=agent_id,
            version_number=next_ver,
            prompt_template=prompt_template,
            is_active=True
        )
        self.session.add(new_version)
        await self.session.flush()
        await self.session.refresh(new_version)
        return new_version

    async def get_prompt_versions(self, tenant_id: str, agent_id: str) -> list[LLMPromptVersion]:
        result = await self.session.execute(
            select(LLMPromptVersion)
            .where(LLMPromptVersion.tenant_id == tenant_id, LLMPromptVersion.agent_id == agent_id)
            .order_by(LLMPromptVersion.version_number.desc())
        )
        return list(result.scalars().all())

    async def rollback_prompt_version(self, tenant_id: str, agent_id: str, target_version: int) -> Optional[LLMPromptVersion]:
        target = await self.session.scalar(
            select(LLMPromptVersion)
            .where(and_(
                LLMPromptVersion.tenant_id == tenant_id, 
                LLMPromptVersion.agent_id == agent_id, 
                LLMPromptVersion.version_number == target_version
            ))
        )
        if not target:
            return None
            
        await self.session.execute(
            text("UPDATE llm_prompt_version SET is_active = FALSE WHERE tenant_id = :tid AND agent_id = :aid AND is_active = TRUE"),
            {"tid": tenant_id, "aid": agent_id}
        )
        target.is_active = True
        await self.session.flush()
        return target