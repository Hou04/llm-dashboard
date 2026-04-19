"""ExplanationRepository — reads and writes M4 explanation and recommendation data."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from modules.detection.models_explainer import (
    LLMTokenExplanation,
    LLMTokenRecommendation,
)


class ExplanationRepository:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_anomaly_id(
        self, anomaly_id: uuid.UUID
    ) -> Optional[LLMTokenExplanation]:
        """Fetch explanation for an anomaly. None if not yet generated."""
        result = await self.session.execute(
            select(LLMTokenExplanation).where(
                LLMTokenExplanation.anomaly_id == anomaly_id
            )
        )
        return result.scalar_one_or_none()

    async def create_explanation(
        self, data: dict
    ) -> LLMTokenExplanation:
        """Insert a new explanation record."""
        explanation = LLMTokenExplanation(**data)
        self.session.add(explanation)
        await self.session.flush()
        await self.session.refresh(explanation)
        return explanation

    async def create_recommendations(
        self, recommendations: list[dict]
    ) -> list[LLMTokenRecommendation]:
        """Insert multiple recommendation records for one explanation."""
        records = []
        for rec_data in recommendations:
            rec = LLMTokenRecommendation(**rec_data)
            self.session.add(rec)
            records.append(rec)
        await self.session.flush()
        return records

    async def get_recommendations_for_anomaly(
        self, anomaly_id: uuid.UUID
    ) -> list[LLMTokenRecommendation]:
        """Fetch all recommendations for an anomaly, priority order."""
        result = await self.session.execute(
            select(LLMTokenRecommendation)
            .where(LLMTokenRecommendation.anomaly_id == anomaly_id)
            .order_by(LLMTokenRecommendation.priority.asc())
        )
        return list(result.scalars().all())

    async def get_recent_for_tenant(
        self, tenant_id: str, limit: int = 10
    ) -> list[LLMTokenExplanation]:
        """Fetch recent explanations for a tenant."""
        result = await self.session.execute(
            select(LLMTokenExplanation)
            .where(LLMTokenExplanation.tenant_id == tenant_id)
            .order_by(LLMTokenExplanation.generated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_recommendation_status(
        self, recommendation_id: uuid.UUID, status: str
    ) -> bool:
        """Update a recommendation status (applied / rejected)."""
        rec = await self.session.get(LLMTokenRecommendation, recommendation_id)
        if rec is None:
            return False
        rec.status = status
        await self.session.flush()
        return True

    async def find_similar_explanations(
        self, tenant_id: str, embedding: list[float], limit: int = 5
    ) -> list[LLMTokenExplanation]:
        """Fetch semantically similar past explanations."""
        result = await self.session.execute(
            select(LLMTokenExplanation)
            .where(
                and_(
                    LLMTokenExplanation.tenant_id == tenant_id,
                    LLMTokenExplanation.embedding != None  # noqa: E711
                )
            )
            .order_by(LLMTokenExplanation.embedding.l2_distance(embedding))
            .limit(limit)
        )
        return list(result.scalars().all())