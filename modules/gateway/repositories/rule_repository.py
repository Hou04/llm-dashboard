"""
RuleRepository — all database operations for llm_governance_rules.

This is the ONLY place in the project that reads from or writes to
the llm_governance_rules table. The governance engine reads rules
from Redis cache (backed by this repository). Admin operations write
rules through this repository.

Design principles:
- Every method receives an AsyncSession via __init__.
- Methods never commit — callers control transaction boundaries.
- Rule deactivation is always a soft delete (is_active=False).
  Rules are audit records. They are never hard-deleted.
- "Active" means: is_active=True AND (effective_from is null OR <= now)
  AND (effective_until is null OR >= now).
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, and_, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from modules.gateway.models import LLMGovernanceRule


class RuleRepository:
    """
    Repository for llm_governance_rules.

    Usage:
        repo = RuleRepository(session)
        rules = await repo.get_active_rules_for_tenant("tenant_abc")
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ================================================================
    # WRITES
    # ================================================================

    async def create(self, rule: LLMGovernanceRule) -> LLMGovernanceRule:
        """
        Insert a new governance rule.

        Args:
            rule: LLMGovernanceRule instance with all required fields set.

        Returns:
            The saved rule with DB-generated values populated.
        """
        self.session.add(rule)
        await self.session.flush()
        await self.session.refresh(rule)
        return rule

    async def update(self, rule: LLMGovernanceRule) -> LLMGovernanceRule:
        """
        Update an existing governance rule.

        The rule must have been loaded from the database in the current
        session (i.e. fetched via get_by_id first). SQLAlchemy tracks
        changes to the object and generates the UPDATE automatically.

        Args:
            rule: Modified LLMGovernanceRule instance.

        Returns:
            The updated rule.
        """
        rule.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        await self.session.refresh(rule)
        return rule

    async def deactivate(self, rule_id: uuid.UUID) -> bool:
        """
        Soft-delete a rule by setting is_active=False.

        Rules are NEVER hard-deleted — they are part of the audit trail.
        Deactivated rules remain in the database and can be reactivated.

        Args:
            rule_id: UUID of the rule to deactivate.

        Returns:
            True if a rule was found and deactivated, False if not found.
        """
        result = await self.session.execute(
            update(LLMGovernanceRule)
            .where(LLMGovernanceRule.id == rule_id)
            .values(
                is_active=False,
                updated_at=datetime.now(timezone.utc),
            )
            .returning(LLMGovernanceRule.id)
        )
        return result.scalar_one_or_none() is not None

    # ================================================================
    # READS
    # ================================================================

    async def get_by_id(
        self, rule_id: uuid.UUID
    ) -> Optional[LLMGovernanceRule]:
        """
        Fetch a single rule by UUID.

        Returns None if not found. Returns the rule regardless of
        is_active status — use get_active_rules_for_tenant for
        governance enforcement.
        """
        result = await self.session.execute(
            select(LLMGovernanceRule).where(LLMGovernanceRule.id == rule_id)
        )
        return result.scalar_one_or_none()

    async def get_active_rules_for_tenant(
        self, tenant_id: str
    ) -> list[LLMGovernanceRule]:
        """
        Fetch all active rules that apply to a specific tenant.

        A rule applies to a tenant if:
        - tenant_id matches OR tenant_id is NULL (global rule)
        - is_active is True
        - effective_from is NULL or <= now (UTC)
        - effective_until is NULL or >= now (UTC)

        Results are ordered by priority descending — highest priority first.
        The governance engine applies the highest-priority matching rule.

        This is the core read path for every API call going through the
        governance engine. In production it reads from Redis cache, but
        this method is the authoritative source and used for cache warming.

        Args:
            tenant_id: The tenant making the API call.

        Returns:
            List of active LLMGovernanceRule instances, highest priority first.
        """
        now = datetime.now(timezone.utc)

        result = await self.session.execute(
            select(LLMGovernanceRule)
            .where(
                and_(
                    # Rule applies to this tenant or all tenants
                    or_(
                        LLMGovernanceRule.tenant_id == tenant_id,
                        LLMGovernanceRule.tenant_id.is_(None),
                    ),
                    # Rule is active
                    LLMGovernanceRule.is_active.is_(True),
                    # Rule is within its effective date range
                    or_(
                        LLMGovernanceRule.effective_from.is_(None),
                        LLMGovernanceRule.effective_from <= now,
                    ),
                    or_(
                        LLMGovernanceRule.effective_until.is_(None),
                        LLMGovernanceRule.effective_until >= now,
                    ),
                )
            )
            .order_by(LLMGovernanceRule.priority.desc())
        )
        return list(result.scalars().all())

    async def get_all_active(self) -> list[LLMGovernanceRule]:
        """
        Fetch all active rules across all tenants.

        Used for cache warming — when the application starts, all active
        rules are loaded into Redis so the governance engine never has to
        hit the database on the hot path.

        Returns:
            All active LLMGovernanceRule instances, highest priority first.
        """
        now = datetime.now(timezone.utc)

        result = await self.session.execute(
            select(LLMGovernanceRule)
            .where(
                and_(
                    LLMGovernanceRule.is_active.is_(True),
                    or_(
                        LLMGovernanceRule.effective_from.is_(None),
                        LLMGovernanceRule.effective_from <= now,
                    ),
                    or_(
                        LLMGovernanceRule.effective_until.is_(None),
                        LLMGovernanceRule.effective_until >= now,
                    ),
                )
            )
            .order_by(LLMGovernanceRule.priority.desc())
        )
        return list(result.scalars().all())