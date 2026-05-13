
import uuid
from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy import select, update, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from modules.tracing.models import LLMSession, SessionStatus

class SessionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_session_id(self, session_id: str) -> Optional[LLMSession]:
        stmt = select(LLMSession).where(LLMSession.session_id == session_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_and_aggregate(self, 
        session_id: str, 
        tenant_id: str,
        log_id: str,
        tokens: int,
        cost: float,
        duration: int,
        agent_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> LLMSession:
        """
        Upsert a session record and atomicaly update its counters.
        Appends the log_id to the call_chain.
        """
        now = datetime.now(timezone.utc)
        
        # Use PostgreSQL ON CONFLICT to handle race conditions and aggregate
        # Note: SQLAlchemy's PG insert is powerful for this
        from sqlalchemy import func

        stmt = pg_insert(LLMSession).values(
            id=str(uuid.uuid4()),
            session_id=session_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            total_calls=1,
            total_tokens=tokens,
            total_cost_usd=cost,
            total_duration_ms=duration or 0,
            call_chain=func.jsonb_build_array(log_id),
            status=SessionStatus.ACTIVE.value,
            started_at=now,
            metadata_={}
        ).on_conflict_do_update(
            index_elements=[LLMSession.session_id],
            set_={
                "total_calls": LLMSession.total_calls + 1,
                "total_tokens": LLMSession.total_tokens + tokens,
                "total_cost_usd": LLMSession.total_cost_usd + cost,
                "total_duration_ms": LLMSession.total_duration_ms + (duration or 0),
                "call_chain": LLMSession.call_chain.concat(func.jsonb_build_array(log_id)),
            }
        ).returning(LLMSession)
        
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def list_recent(self, tenant_id: str, limit: int = 20) -> List[LLMSession]:
        stmt = select(LLMSession).where(LLMSession.tenant_id == tenant_id).order_by(LLMSession.started_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
