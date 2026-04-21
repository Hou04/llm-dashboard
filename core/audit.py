"""
Audit Service — records admin actions for compliance.

Usage in any router:
    from core.audit import audit

    @router.post("/rules")
    async def create_rule(request, user, session):
        rule = await service.create_rule(...)
        await audit.log(
            session=session,
            user=user,
            action="create",
            resource_type="governance_rule",
            resource_id=str(rule.id),
            description=f"Created rule: {rule.rule_type}",
            details={"rule_type": rule.rule_type, "tenant_id": rule.tenant_id},
        )

The audit service never raises exceptions — logging failures
are silently captured to prevent audit issues from breaking
business operations.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Any
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from modules.auth.schemas import CurrentUser

logger = logging.getLogger(__name__)


class AuditService:
    """Append-only audit logger for admin actions."""

    async def log(
        self,
        session: AsyncSession,
        user: CurrentUser,
        action: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        description: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        status: str = "success",
    ) -> Optional[str]:
        """
        Record an audit event.

        Returns the audit log entry ID, or None if logging failed.
        Never raises — failures are logged and swallowed.
        """
        try:
            from core.audit_model import LLMAuditLog

            entry = LLMAuditLog(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(timezone.utc),
                user_id=user.id,
                username=user.username,
                role=user.role,
                tenant_id=user.tenant_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                description=description,
                details=details,
                ip_address=ip_address,
                user_agent=user_agent,
                status=status,
            )
            session.add(entry)
            # Don't commit — let the request lifecycle handle it
            await session.flush()

            logger.info(
                f"audit.recorded action={action} resource={resource_type} "
                f"resource_id={resource_id} user={user.username}"
            )
            return entry.id

        except Exception as e:
            # Never let audit failures break business logic
            logger.error(
                f"audit.failed action={action} resource={resource_type} "
                f"user={user.username} error={e}"
            )
            return None

    async def query(
        self,
        session: AsyncSession,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> list[dict]:
        """
        Query the audit log with optional filters.
        Returns a list of audit entries as dicts.
        """
        try:
            from sqlalchemy import select, desc
            from core.audit_model import LLMAuditLog

            stmt = select(LLMAuditLog).order_by(desc(LLMAuditLog.timestamp))

            if action:
                stmt = stmt.where(LLMAuditLog.action == action)
            if resource_type:
                stmt = stmt.where(LLMAuditLog.resource_type == resource_type)
            if user_id:
                stmt = stmt.where(LLMAuditLog.user_id == user_id)
            if tenant_id:
                stmt = stmt.where(LLMAuditLog.tenant_id == tenant_id)

            stmt = stmt.offset(skip).limit(limit)
            result = await session.execute(stmt)
            rows = result.scalars().all()

            return [
                {
                    "id": r.id,
                    "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                    "user_id": r.user_id,
                    "username": r.username,
                    "role": r.role,
                    "tenant_id": r.tenant_id,
                    "action": r.action,
                    "resource_type": r.resource_type,
                    "resource_id": r.resource_id,
                    "description": r.description,
                    "details": r.details,
                    "status": r.status,
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"audit.query_failed error={e}")
            return []


# Singleton
audit = AuditService()
