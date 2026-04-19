"""AnomalyRepository — reads and writes anomaly records."""

import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select, and_, update
from sqlalchemy.ext.asyncio import AsyncSession

from modules.detection.models import LLMAnomalyRecord


class AnomalyRepository:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, anomaly: LLMAnomalyRecord) -> LLMAnomalyRecord:
        self.session.add(anomaly)
        await self.session.flush()
        await self.session.refresh(anomaly)
        return anomaly

    async def has_recent_anomaly(
        self, tenant_id: str, anomaly_type: str, within_minutes: int = 60
    ) -> bool:
        """Dedup check — prevent one anomaly per API call spam."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
        result = await self.session.execute(
            select(LLMAnomalyRecord.id)
            .where(
                and_(
                    LLMAnomalyRecord.tenant_id == tenant_id,
                    LLMAnomalyRecord.anomaly_type == anomaly_type,
                    LLMAnomalyRecord.detected_at >= cutoff,
                    LLMAnomalyRecord.resolved.is_(False),
                )
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def get_recent_for_tenant(
        self,
        tenant_id: str,
        limit: int = 20,
        include_resolved: bool = False,
    ) -> list[LLMAnomalyRecord]:
        conditions = [LLMAnomalyRecord.tenant_id == tenant_id]
        if not include_resolved:
            conditions.append(LLMAnomalyRecord.resolved.is_(False))
        result = await self.session.execute(
            select(LLMAnomalyRecord)
            .where(and_(*conditions))
            .order_by(LLMAnomalyRecord.detected_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_all_recent(
        self, hours: int = 24, severity: Optional[str] = None
    ) -> list[LLMAnomalyRecord]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        conditions = [LLMAnomalyRecord.detected_at >= cutoff]
        if severity:
            conditions.append(LLMAnomalyRecord.severity == severity)
        result = await self.session.execute(
            select(LLMAnomalyRecord)
            .where(and_(*conditions))
            .order_by(LLMAnomalyRecord.detected_at.desc())
        )
        return list(result.scalars().all())

    async def resolve(self, anomaly_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            update(LLMAnomalyRecord)
            .where(LLMAnomalyRecord.id == anomaly_id)
            .values(resolved=True, resolved_at=datetime.now(timezone.utc))
            .returning(LLMAnomalyRecord.id)
        )
        await self.session.flush()
        return result.scalar_one_or_none() is not None