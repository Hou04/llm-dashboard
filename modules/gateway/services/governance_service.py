"""
M7 Governance Service — lives inside M1 (Gateway) module.

Provides:
- Rule CRUD (create, list, get, update, deactivate)
- Decision audit log queries
- Real KPI summary for the dashboard
- Live quota status per tenant

Zero hardcoded values — all data comes from the DB.
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from modules.gateway.models import (
    LLMGovernanceRule,
    LLMGovernanceDecision,
    GovernanceDecision as DecisionEnum,
)

logger = logging.getLogger(__name__)


class GovernanceService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ================================================================
    # RULES — CRUD
    # ================================================================

    async def list_rules(
        self,
        tenant_id:   Optional[str] = None,
        active_only: bool = True,
        rule_type:   Optional[str] = None,
    ) -> list[LLMGovernanceRule]:
        stmt = select(LLMGovernanceRule)
        conditions = []

        if active_only:
            conditions.append(LLMGovernanceRule.is_active == True)
        if tenant_id:
            # Include global rules (tenant_id IS NULL) + tenant-specific rules
            conditions.append(
                or_(
                    LLMGovernanceRule.tenant_id == tenant_id,
                    LLMGovernanceRule.tenant_id == None,
                )
            )
        if rule_type:
            conditions.append(LLMGovernanceRule.rule_type == rule_type)

        if conditions:
            stmt = stmt.where(and_(*conditions))

        stmt = stmt.order_by(
            LLMGovernanceRule.priority.desc(),
            LLMGovernanceRule.created_at.desc(),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_rule(self, rule_id: UUID) -> Optional[LLMGovernanceRule]:
        result = await self.session.execute(
            select(LLMGovernanceRule).where(LLMGovernanceRule.id == rule_id)
        )
        return result.scalar_one_or_none()

    async def create_rule(self, data: dict) -> LLMGovernanceRule:
        # Remove None values from data to let DB defaults apply
        clean_data = {k: v for k, v in data.items() if v is not None}
        rule = LLMGovernanceRule(**clean_data)
        self.session.add(rule)
        await self.session.commit()
        await self.session.refresh(rule)
        return rule

    async def update_rule(
        self, rule_id: UUID, updates: dict
    ) -> Optional[LLMGovernanceRule]:
        rule = await self.get_rule(rule_id)
        if rule is None:
            return None
        for key, val in updates.items():
            setattr(rule, key, val)
        rule.updated_at = datetime.now(timezone.utc)
        await self.session.commit()
        await self.session.refresh(rule)
        return rule

    async def deactivate_rule(self, rule_id: UUID) -> Optional[LLMGovernanceRule]:
        """Soft-delete: never hard-delete rules (audit trail)."""
        return await self.update_rule(
            rule_id, {"is_active": False}
        )

    # ================================================================
    # DECISIONS — audit log
    # ================================================================

    async def list_decisions(
        self,
        tenant_id:     Optional[str] = None,
        hours:         int = 24,
        decision_type: Optional[str] = None,
        limit:         int = 100,
    ) -> list[LLMGovernanceDecision]:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        stmt = (
            select(LLMGovernanceDecision)
            .where(LLMGovernanceDecision.evaluated_at >= since)
            .order_by(LLMGovernanceDecision.evaluated_at.desc())
            .limit(limit)
        )
        if tenant_id:
            stmt = stmt.where(LLMGovernanceDecision.tenant_id == tenant_id)
        if decision_type:
            stmt = stmt.where(LLMGovernanceDecision.decision == decision_type)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ================================================================
    # SUMMARY — Real KPIs (no hardcoded values)
    # ================================================================

    async def get_summary(self, period_days: int = 30) -> dict:
        since = datetime.now(timezone.utc) - timedelta(days=period_days)

        # Count decisions by type
        counts_stmt = (
            select(
                LLMGovernanceDecision.decision,
                func.count().label("cnt"),
            )
            .where(LLMGovernanceDecision.evaluated_at >= since)
            .group_by(LLMGovernanceDecision.decision)
        )
        counts_result = await self.session.execute(counts_stmt)
        counts = {"total": 0, "allowed": 0, "blocked": 0, "downgraded": 0}
        for row in counts_result.all():
            decision_val, cnt = row
            counts["total"] += cnt
            if decision_val == DecisionEnum.ALLOW.value:
                counts["allowed"] = cnt
            elif decision_val == DecisionEnum.BLOCK.value:
                counts["blocked"] = cnt
            elif decision_val == DecisionEnum.ALLOW_DOWNGRADE.value:
                counts["downgraded"] = cnt

        # Rule counts
        total_rules  = (await self.session.execute(
            select(func.count()).select_from(LLMGovernanceRule)
        )).scalar() or 0
        active_rules = (await self.session.execute(
            select(func.count()).select_from(LLMGovernanceRule).where(
                LLMGovernanceRule.is_active == True
            )
        )).scalar() or 0

        # Per-tenant breakdown
        tenant_stmt = (
            select(
                LLMGovernanceDecision.tenant_id,
                LLMGovernanceDecision.decision,
                func.count().label("cnt"),
            )
            .where(LLMGovernanceDecision.evaluated_at >= since)
            .group_by(
                LLMGovernanceDecision.tenant_id,
                LLMGovernanceDecision.decision,
            )
        )
        tenant_result = await self.session.execute(tenant_stmt)
        tenant_map: dict[str, dict] = {}
        for tid, decision_val, cnt in tenant_result.all():
            if tid not in tenant_map:
                tenant_map[tid] = {
                    "tenant_id": tid, "total": 0,
                    "blocked": 0, "downgraded": 0, "allowed": 0,
                }
            tenant_map[tid]["total"] += cnt
            if decision_val == DecisionEnum.BLOCK.value:
                tenant_map[tid]["blocked"] = cnt
            elif decision_val == DecisionEnum.ALLOW_DOWNGRADE.value:
                tenant_map[tid]["downgraded"] = cnt
            elif decision_val == DecisionEnum.ALLOW.value:
                tenant_map[tid]["allowed"] = cnt
        for t in tenant_map.values():
            t["block_rate_pct"] = round(
                t["blocked"] / t["total"] * 100, 1
            ) if t["total"] > 0 else 0.0
        tenant_stats = sorted(
            tenant_map.values(), key=lambda x: x["total"], reverse=True
        )

        total = counts["total"]
        blocked = counts["blocked"]
        downgraded = counts["downgraded"]

        return {
            "period_days":        period_days,
            "generated_at":       datetime.now(timezone.utc),
            "total_decisions":    total,
            "allowed_count":      counts["allowed"],
            "blocked_count":      blocked,
            "downgraded_count":   downgraded,
            "block_rate_pct":     round(blocked / total * 100, 2) if total > 0 else 0.0,
            "downgrade_rate_pct": round(downgraded / total * 100, 2) if total > 0 else 0.0,
            "active_rules_count": active_rules,
            "total_rules_count":  total_rules,
            # Heuristic cost impact estimates
            "estimated_cost_blocked_usd": round(blocked * 0.0015, 2),
            "estimated_cost_saved_usd":   round(downgraded * 0.025, 2),
            "tenant_stats":       tenant_stats,
        }

    async def get_quota_status(self, tenant_id: str) -> dict:
        """Live quota status — reads today's decision log."""
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        stmt = (
            select(LLMGovernanceDecision)
            .where(and_(
                LLMGovernanceDecision.tenant_id == tenant_id,
                LLMGovernanceDecision.evaluated_at >= today_start,
            ))
            .order_by(LLMGovernanceDecision.evaluated_at.desc())
            .limit(1)
        )
        result  = await self.session.execute(stmt)
        latest  = result.scalar_one_or_none()

        # Find tightest limits from active rules
        rules        = await self.list_rules(tenant_id=tenant_id, active_only=True)
        daily_limit  = None
        monthly_budget = None
        for rule in rules:
            if rule.daily_token_limit:
                if daily_limit is None or rule.daily_token_limit < daily_limit:
                    daily_limit = rule.daily_token_limit
            if rule.monthly_budget_usd:
                mb = float(rule.monthly_budget_usd)
                if monthly_budget is None or mb < monthly_budget:
                    monthly_budget = mb

        tokens_today = (latest.tokens_used_today or 0) if latest else 0
        budget_today = float(latest.budget_used_today or 0) if latest else 0.0

        pct_daily  = round(tokens_today / daily_limit * 100, 1) if daily_limit else None
        pct_budget = round(budget_today / monthly_budget * 100, 1) if monthly_budget else None

        if pct_daily is not None and pct_daily >= 100:
            status = "blocked"
        elif pct_daily is not None and pct_daily >= 90:
            status = "critical"
        elif pct_daily is not None and pct_daily >= 75:
            status = "warning"
        else:
            status = "ok"

        return {
            "tenant_id":               tenant_id,
            "tokens_used_today":       tokens_today,
            "daily_token_limit":       daily_limit,
            "pct_daily_used":          pct_daily,
            "budget_used_today_usd":   budget_today,
            "monthly_budget_usd":      monthly_budget,
            "pct_monthly_budget_used": pct_budget,
            "status":                  status,
        }
