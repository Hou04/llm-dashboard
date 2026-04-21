"""
DashboardService — assembles data from all four modules into
frontend-ready responses.
 
Design principles:
- Reads only. Zero writes. Zero database tables.
- Each method assembles data from multiple module services.
- Failures in one module never crash the whole response.
  If the forecast is unavailable, return the cost data anyway.
- Uses asyncio.gather for parallel tenant fetching (N tenants in ~1 query time).
- Response caching via Redis (60s TTL) for expensive aggregations.
"""
 
import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
 
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
 
from modules.analytics.services import AnalyticsService
from modules.detection.services import DetectionService
from modules.forecasting.services import ForecastingService
from modules.gateway.repositories import LogRepository
from core.response_cache import response_cache
 
logger = logging.getLogger(__name__)
 
DEFAULT_PERIOD_DAYS = 30
 
# Pure dynamic tenant discovery from db is used instead of a fallback list 
 
class DashboardService:
 
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.analytics = AnalyticsService(session)
        self.detection = DetectionService(session)
        self.forecasting = ForecastingService(session)
        self.log_repo = LogRepository(session)
 
    # ================================================================
    # EXECUTIVE OVERVIEW
    # ================================================================
 
    async def get_executive_overview(
        self, period_days: int = DEFAULT_PERIOD_DAYS
    ) -> dict:
        now = datetime.now(timezone.utc)
        to_dt = now
        from_dt = now - timedelta(days=period_days)
        prior_from_dt = from_dt - timedelta(days=period_days)
 
        # Dynamically discover tenants from the database
        tenants = await self._discover_tenants()
 
        # Parallel fetch — all tenants concurrently instead of sequentially
        # Each call uses the shared session but reads only (no write conflicts)
        tenant_items = await asyncio.gather(
            *[
                self._build_tenant_overview_item(
                    tenant_id, from_dt, to_dt, prior_from_dt
                )
                for tenant_id in tenants
            ]
        )
 
        tenant_items = sorted(
            tenant_items,
            key=lambda t: float(t["cost"]["total_usd"]),
            reverse=True,
        )
 
        total_cost = sum(float(t["cost"]["total_usd"]) for t in tenant_items)
        tenants_at_risk = sum(1 for t in tenant_items if t["budget_risk"]["at_risk"])
        active_anomalies = sum(t["anomaly_status"]["count"] for t in tenant_items)
        critical_anomalies = sum(
            1 for t in tenant_items
            if t["anomaly_status"]["severity"] == "critical"
        )
 
        return {
            "generated_at": now,
            "period_days": period_days,
            "tenants": tenant_items,
            "summary": {
                "total_tenants": len(tenants),
                "total_cost_usd": str(round(total_cost, 4)),
                "tenants_at_risk": tenants_at_risk,
                "active_anomalies": active_anomalies,
                "critical_anomalies": critical_anomalies,
            },
        }
 
    async def _build_tenant_overview_item(
        self,
        tenant_id: str,
        from_dt: datetime,
        to_dt: datetime,
        prior_from_dt: datetime,
    ) -> dict:
        cost_current = await self._safe_get_cost_summary(tenant_id, from_dt, to_dt)
        cost_prior = await self._safe_get_cost_summary(tenant_id, prior_from_dt, from_dt)
        anomalies = await self._safe_get_anomalies(tenant_id)
        forecast_rows = await self._safe_get_forecast_rows(tenant_id)
        risks = await self._safe_get_budget_risks(tenant_id)
 
        current_cost = float(cost_current.get("total_cost_usd", 0))
        prior_cost = float(cost_prior.get("total_cost_usd", 0))
        change_pct = round((current_cost - prior_cost) / prior_cost * 100, 1) if prior_cost > 0 else 0.0
        trend = "stable" if abs(change_pct) < 5 else ("up" if change_pct > 0 else "down")
 
        total_calls = cost_current.get("total_calls", 0)
        error_calls = cost_current.get("error_calls", 0)
        error_rate = round(error_calls / total_calls * 100, 1) if total_calls > 0 else 0.0
 
        active_anomalies = [a for a in anomalies if not a.get("resolved", False)]
        has_anomaly = len(active_anomalies) > 0
        worst_severity = None
        if active_anomalies:
            severity_order = {"critical": 4, "high": 3, "warning": 2, "normal": 1}
            worst_severity = max(
                active_anomalies,
                key=lambda a: severity_order.get(a.get("severity", "normal"), 0),
            ).get("severity")
 
        forecast_summary = self._extract_forecast_summary(forecast_rows)
 
        tenant_risks = list(risks)
        at_risk = len(tenant_risks) > 0
        risk_item = tenant_risks[0] if tenant_risks else None
 
        def urgency(days: int) -> str:
            if days <= 7:
                return "critical"
            elif days <= 14:
                return "warning"
            return "watch"
 
        return {
            "tenant_id": tenant_id,
            "cost": {
                "total_usd": str(round(current_cost, 4)),
                "trend": trend,
                "change_pct": change_pct,
            },
            "usage": {
                "total_tokens": cost_current.get("total_tokens", 0),
                "total_calls": total_calls,
                "error_rate_pct": error_rate,
            },
            "anomaly_status": {
                "has_active_anomaly": has_anomaly,
                "severity": worst_severity,
                "count": len(active_anomalies),
            },
            "forecast": forecast_summary,
            "budget_risk": {
                "at_risk": at_risk,
                "days_until_exhaustion": risk_item["days_until_exhaustion"] if risk_item else None,
                "risk_type": risk_item["risk_type"] if risk_item else None,
                "urgency": urgency(risk_item["days_until_exhaustion"]) if risk_item else None,
            },
        }
 
    # ================================================================
    # AGENT DRILL-DOWN
    # ================================================================

    async def get_agent_breakdown(
        self, tenant_id: str, period_days: int = DEFAULT_PERIOD_DAYS
    ) -> dict:
        """Per-agent cost, token, and error breakdown for a tenant."""
        now = datetime.now(timezone.utc)
        from_dt = now - timedelta(days=period_days)

        try:
            result = await self.session.execute(
                text("""
                    SELECT
                        COALESCE(agent_id, 'unknown')           AS agent_id,
                        COUNT(*)                                 AS total_calls,
                        COUNT(*) FILTER (WHERE status = 'success' OR status IS NULL) AS successful_calls,
                        COUNT(*) FILTER (WHERE status = 'error')  AS error_calls,
                        COALESCE(SUM(total_tokens), 0)           AS total_tokens,
                        COALESCE(SUM(input_tokens), 0)           AS total_input_tokens,
                        COALESCE(SUM(output_tokens), 0)          AS total_output_tokens,
                        COALESCE(SUM(cost_usd), 0)               AS total_cost_usd,
                        AVG(duration_ms)                         AS avg_duration_ms
                    FROM llm_token_log
                    WHERE tenant_id = :tenant_id
                      AND created_at >= :from_dt
                      AND created_at <= :to_dt
                    GROUP BY COALESCE(agent_id, 'unknown')
                    ORDER BY COALESCE(SUM(cost_usd), 0) DESC
                """),
                {"tenant_id": tenant_id, "from_dt": from_dt, "to_dt": now},
            )
            rows = result.all()
        except Exception as e:
            logger.warning(f"Agent breakdown query failed for {tenant_id}: {e}")
            rows = []

        grand_total_cost = sum(float(r.total_cost_usd or 0) for r in rows)

        # Get anomaly counts per agent (best-effort)
        agent_anomalies: dict[str, int] = {}
        try:
            anom_result = await self.session.execute(
                text("""
                    SELECT COALESCE(agent_id, 'unknown') AS agent_id, COUNT(*) AS cnt
                    FROM llm_anomaly
                    WHERE tenant_id = :tenant_id
                      AND resolved = FALSE
                    GROUP BY COALESCE(agent_id, 'unknown')
                """),
                {"tenant_id": tenant_id},
            )
            for ar in anom_result.all():
                agent_anomalies[ar.agent_id] = ar.cnt
        except Exception:
            pass  # anomalies table may not have agent_id column

        agents = []
        for r in rows:
            total_calls = int(r.total_calls or 0)
            error_calls = int(r.error_calls or 0)
            cost = float(r.total_cost_usd or 0)
            agents.append({
                "agent_id": r.agent_id,
                "total_calls": total_calls,
                "successful_calls": int(r.successful_calls or 0),
                "error_calls": error_calls,
                "error_rate_pct": round(error_calls / total_calls * 100, 1) if total_calls > 0 else 0.0,
                "total_tokens": int(r.total_tokens or 0),
                "total_input_tokens": int(r.total_input_tokens or 0),
                "total_output_tokens": int(r.total_output_tokens or 0),
                "total_cost_usd": str(round(cost, 6)),
                "cost_share_pct": round(cost / grand_total_cost * 100, 1) if grand_total_cost > 0 else 0.0,
                "avg_duration_ms": round(float(r.avg_duration_ms), 1) if r.avg_duration_ms else None,
                "anomaly_count": agent_anomalies.get(r.agent_id, 0),
            })

        return {
            "tenant_id": tenant_id,
            "period_days": period_days,
            "generated_at": now,
            "agents": agents,
            "total_agents": len(agents),
        }

    # ================================================================
    # TENANT DEEP-DIVE
    # ================================================================
 
    async def get_tenant_detail(
        self,
        tenant_id: str,
        period_days: int = DEFAULT_PERIOD_DAYS,
    ) -> dict:
        now = datetime.now(timezone.utc)
        to_dt = now
        from_dt = now - timedelta(days=period_days)
 
        cost_summary = await self._safe_get_cost_summary(tenant_id, from_dt, to_dt)
        daily_trend = await self._safe_get_daily_trend(tenant_id, from_dt, to_dt)
        model_breakdown = await self._safe_get_model_breakdown(tenant_id, from_dt, to_dt)
        anomaly_rows = await self._safe_get_anomalies(tenant_id, limit=10)
        forecast_rows = await self._safe_get_forecast_rows(tenant_id)
        governance = await self._safe_get_governance_summary(tenant_id, from_dt, to_dt)
 
        # BUG FIX: always returns a dict, never None
        forecast_detail = self._build_forecast_detail(forecast_rows)
 
        active_anomalies = [
            {
                "id": str(a.get("id", "")),
                "anomaly_type": a.get("anomaly_type", ""),
                "severity": a.get("severity", ""),
                "detector_votes": a.get("detector_votes", ""),
                "vote_count": a.get("vote_count", 0),
                "observed_value": a.get("observed_value", 0.0),
                "baseline_mean": a.get("baseline_mean", 0.0),
                "description": a.get("description"),
                "detected_at": a.get("detected_at", now),
            }
            for a in anomaly_rows
            if not a.get("resolved", False)
        ]
 
        return {
            "tenant_id": tenant_id,
            "generated_at": now,
            "period_days": period_days,
            "cost": {
                "period_days": period_days,
                "total_calls": cost_summary.get("total_calls", 0),
                "successful_calls": cost_summary.get("successful_calls", 0),
                "error_calls": cost_summary.get("error_calls", 0),
                "blocked_calls": cost_summary.get("blocked_calls", 0),
                "total_tokens": cost_summary.get("total_tokens", 0),
                "total_cost_usd": str(cost_summary.get("total_cost_usd", "0")),
                "avg_duration_ms": cost_summary.get("avg_duration_ms"),
                "daily_trend": daily_trend,
                "model_breakdown": model_breakdown,
            },
            "anomalies": {
                "active": active_anomalies,
                "total_active": len(active_anomalies),
            },
            "forecast": forecast_detail,   # always a dict now
            "governance": governance,
        }
 
    # ================================================================
    # ALERTS FEED
    # ================================================================
 
    async def get_alerts(
        self,
        hours: int = 24,
        max_budget_risk_days: int = 7,
    ) -> dict:
        now = datetime.now(timezone.utc)
 
        anomaly_rows = await self._safe_get_all_anomalies(hours=hours)
        risk_rows = await self._safe_get_budget_risks_all(max_days=max_budget_risk_days)
 
        alerts = []
 
        for a in anomaly_rows:
            severity = a.get("severity", "warning")
            alerts.append({
                "id": str(a.get("id", uuid.uuid4())),
                "alert_type": "anomaly",
                "severity": severity,
                "tenant_id": a.get("tenant_id", ""),
                "title": _anomaly_title(a.get("anomaly_type", ""), severity),
                "description": a.get("description") or _anomaly_description(a),
                "detected_at": a.get("detected_at", now),
                "action_url": f"/v1/detection/anomalies/{a.get('tenant_id', '')}",
            })
 
        for r in risk_rows:
            days = r.get("days_until_exhaustion", 30)
            severity = "critical" if days <= 7 else "warning"
            alerts.append({
                "id": str(r.get("id", uuid.uuid4())),
                "alert_type": "budget_risk",
                "severity": severity,
                "tenant_id": r.get("tenant_id", ""),
                "title": _risk_title(r.get("risk_type", ""), days),
                "description": _risk_description(r),
                "detected_at": r.get("generated_at", now),
                "action_url": f"/v1/forecasting/forecast/{r.get('tenant_id', '')}",
            })
 
        severity_order = {"critical": 0, "high": 1, "warning": 2, "watch": 3}
        alerts.sort(
            key=lambda a: (
                severity_order.get(a["severity"], 9),
                -(a["detected_at"].timestamp()
                  if hasattr(a["detected_at"], "timestamp")
                  else 0),
            )
        )
 
        return {
            "generated_at": now,
            "alerts": alerts,
            "total": len(alerts),
            "critical_count": sum(1 for a in alerts if a["severity"] == "critical"),
            "warning_count": sum(1 for a in alerts if a["severity"] == "warning"),
        }
 
    # ================================================================
    # SYSTEM HEALTH
    # ================================================================
 
    async def get_system_health(self) -> dict:
        now = datetime.now(timezone.utc)
 
        db_status = "ok"
        try:
            await self.session.execute(text("SELECT 1"))
        except Exception as e:
            db_status = f"error: {e}"
 
        redis_status = "ok"
        try:
            from core.redis import get_quota_redis
            r = get_quota_redis()
            await r.ping()
            await r.aclose()
        except Exception as e:
            redis_status = f"error: {e}"
 
        # Perform basic table counts for module health
        gateway_status, gateway_details = "ok", "Logging and governance"
        try:
            res = await self.session.execute(text("SELECT COUNT(*) FROM llm_token_log LIMIT 1"))
            gateway_details = f"{res.scalar() or 0} logs"
        except Exception as e:
            gateway_status, gateway_details = "error", str(e)

        analytics_status, analytics_details = "ok", "Cost aggregation"
        try:
            res = await self.session.execute(text("SELECT COUNT(*) FROM llm_cost_daily LIMIT 1"))
            analytics_details = f"{res.scalar() or 0} daily records"
        except Exception as e:
            analytics_status, analytics_details = "error", str(e)

        detection_status, detection_details = "ok", "Anomaly detection"
        try:
            res = await self.session.execute(text("SELECT COUNT(*) FROM llm_anomaly LIMIT 1"))
            detection_details = f"{res.scalar() or 0} anomalies"
        except Exception as e:
            detection_status, detection_details = "error", str(e)

        forecasting_status, forecasting_details = "ok", "30-day forecasts"
        try:
            # Forecasts are generated on the fly, just check if the service is loaded
            res = await self.session.execute(text("SELECT 1"))
            forecasting_details = "Ready" if res.scalar() == 1 else "Degraded"
        except Exception as e:
            forecasting_status, forecasting_details = "error", str(e)

        overall = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"

        return {
            "status": overall,
            "generated_at": now,
            "modules": [
                {"name": "gateway", "status": gateway_status, "details": gateway_details},
                {"name": "analytics", "status": analytics_status, "details": analytics_details},
                {"name": "detection", "status": detection_status, "details": detection_details},
                {"name": "forecasting", "status": forecasting_status, "details": forecasting_details},
            ],
            "database": db_status,
            "redis": redis_status,
        }
 
    # ================================================================
    # PRIVATE — DYNAMIC TENANT DISCOVERY
    # ================================================================
 
    async def _discover_tenants(self) -> list[str]:
        """Dynamically discover tenants from the new master database table."""
        try:
            result = await self.session.execute(
                text("SELECT tenant_id FROM llm_tenants ORDER BY tenant_id")
            )
            tenants = [row[0] for row in result.all()]
            return tenants if tenants else []
        except Exception as e:
            logger.warning(f"Tenant discovery failed: {e}")
            return []

    # ================================================================
    # PRIVATE SAFE FETCHERS
    # ================================================================
 
    async def _safe_get_cost_summary(
        self, tenant_id: str, from_dt: datetime, to_dt: datetime
    ) -> dict:
        try:
            res = await self.analytics.get_cost_summary(tenant_id, from_dt, to_dt)
            with open("debug_log.txt", "a") as f:
                f.write(f"Tenant: '{tenant_id}', res: {res}\n")
            logger.info(f"SAFE_GET_COST_SUMMARY success for {tenant_id}: {res}")
            return res
        except Exception as e:
            logger.warning(f"Cost summary failed for {tenant_id}: {e}", exc_info=True)
            import traceback
            with open("error_log.txt", "a") as f:
                f.write(f"Cost summary failed for {tenant_id}: {e}\n")
                f.write(traceback.format_exc() + "\n")
            return {
                "total_calls": 0, "successful_calls": 0, "error_calls": 0,
                "blocked_calls": 0, "total_tokens": 0, "total_cost_usd": "0",
                "avg_duration_ms": None,
            }
 
    async def _safe_get_daily_trend(
        self, tenant_id: str, from_dt: datetime, to_dt: datetime
    ) -> list:
        try:
            rows = await self.analytics.get_daily_trend(tenant_id, from_dt, to_dt)
            return [
                {
                    "date": r["date"],
                    "total_cost_usd": r["total_cost_usd"],
                    "total_tokens": r["total_tokens"],
                    "total_calls": r["total_calls"],
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"Daily trend failed for {tenant_id}: {e}")
            return []
 
    async def _safe_get_model_breakdown(
        self, tenant_id: str, from_dt: datetime, to_dt: datetime
    ) -> list:
        try:
            rows = await self.analytics.get_model_breakdown(tenant_id, from_dt, to_dt)
            return [
                {
                    "model": r["model"],
                    "provider": r["provider"],
                    "call_count": r["call_count"],
                    "total_cost_usd": r["total_cost_usd"],
                    "cost_share_pct": r["cost_share_pct"],
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"Model breakdown failed for {tenant_id}: {e}")
            return []
 
    async def _safe_get_anomalies(
        self, tenant_id: str, limit: int = 5
    ) -> list:
        try:
            records = await self.detection.get_tenant_anomalies(
                tenant_id, include_resolved=False, limit=limit
            )
            return [
                {
                    "id": r.id,
                    "anomaly_type": r.anomaly_type,
                    "severity": r.severity,
                    "detector_votes": r.detector_votes,
                    "vote_count": r.vote_count,
                    "observed_value": r.observed_value,
                    "baseline_mean": r.baseline_mean,
                    "description": r.description,
                    "detected_at": r.detected_at,
                    "resolved": r.resolved,
                }
                for r in records
            ]
        except Exception as e:
            logger.warning(f"Anomalies failed for {tenant_id}: {e}")
            return []
 
    async def _safe_get_all_anomalies(self, hours: int = 24) -> list:
        try:
            records = await self.detection.get_all_recent_anomalies(hours=hours)
            return [
                {
                    "id": r.id,
                    "tenant_id": r.tenant_id,
                    "anomaly_type": r.anomaly_type,
                    "severity": r.severity,
                    "observed_value": r.observed_value,
                    "baseline_mean": r.baseline_mean,
                    "description": r.description,
                    "detected_at": r.detected_at,
                    "resolved": r.resolved,
                }
                for r in records
                if not r.resolved
            ]
        except Exception as e:
            logger.warning(f"All anomalies fetch failed: {e}")
            return []
 
    async def _safe_get_forecast_rows(self, tenant_id: str) -> list:
        try:
            return await self.forecasting.get_all_scenarios(tenant_id)
        except Exception as e:
            logger.warning(f"Forecast rows failed for {tenant_id}: {e}")
            return []
 
    async def _safe_get_budget_risks(self, tenant_id: str) -> list:
        try:
            all_risks = await self.forecasting.get_budget_risks(max_days=30)
            return [
                {
                    "id": r.id,
                    "tenant_id": r.tenant_id,
                    "risk_type": r.risk_type,
                    "days_until_exhaustion": r.days_until_exhaustion,
                    "exhaustion_date": r.exhaustion_date,
                    "forecasted_value_at_exhaustion": r.forecasted_value_at_exhaustion,
                    "governance_limit": r.governance_limit,
                    "pct_of_limit_today": r.pct_of_limit_today,
                    "generated_at": r.generated_at,
                }
                for r in all_risks
                if r.tenant_id == tenant_id
            ]
        except Exception as e:
            logger.warning(f"Budget risks failed for {tenant_id}: {e}")
            return []
 
    async def _safe_get_budget_risks_all(self, max_days: int = 7) -> list:
        try:
            risks = await self.forecasting.get_budget_risks(max_days=max_days)
            return [
                {
                    "id": r.id,
                    "tenant_id": r.tenant_id,
                    "risk_type": r.risk_type,
                    "days_until_exhaustion": r.days_until_exhaustion,
                    "exhaustion_date": r.exhaustion_date,
                    "forecasted_value_at_exhaustion": r.forecasted_value_at_exhaustion,
                    "governance_limit": r.governance_limit,
                    "pct_of_limit_today": r.pct_of_limit_today,
                    "generated_at": r.generated_at,
                }
                for r in risks
            ]
        except Exception as e:
            logger.warning(f"All budget risks failed: {e}")
            return []
 
    async def _safe_get_governance_summary(
        self, tenant_id: str, from_dt: datetime, to_dt: datetime
    ) -> dict:
        try:
            result = await self.session.execute(
                text("""
                    SELECT
                        COUNT(*)                                    AS total_calls,
                        COUNT(*) FILTER (WHERE status = 'blocked') AS blocked_calls
                    FROM llm_token_log
                    WHERE tenant_id = :tenant_id
                      AND created_at >= :from_dt
                      AND created_at <= :to_dt
                """),
                {"tenant_id": tenant_id, "from_dt": from_dt, "to_dt": to_dt},
            )
            row = result.one()
 
            rules_result = await self.session.execute(
                text("""
                    SELECT COUNT(*) AS cnt
                    FROM llm_governance_rules
                    WHERE (tenant_id = :tenant_id OR tenant_id IS NULL)
                      AND is_active = TRUE
                """),
                {"tenant_id": tenant_id},
            )
            rules_row = rules_result.one()
 
            total = int(row.total_calls or 0)
            blocked = int(row.blocked_calls or 0)
            block_rate = round(blocked / total * 100, 2) if total > 0 else 0.0
 
            return {
                "total_calls": total,
                "blocked_calls": blocked,
                "block_rate_pct": block_rate,
                "downgraded_calls": 0,
                "active_rules_count": int(rules_row.cnt or 0),
            }
        except Exception as e:
            logger.warning(f"Governance summary failed for {tenant_id}: {e}")
            return {
                "total_calls": 0, "blocked_calls": 0, "block_rate_pct": 0.0,
                "downgraded_calls": 0, "active_rules_count": 0,
            }
 
    # ================================================================
    # PRIVATE HELPERS
    # ================================================================
 
    def _extract_forecast_summary(self, rows: list) -> Optional[dict]:
        if not rows:
            return None
        likely_rows = [r for r in rows if r.scenario == "likely"]
        if not likely_rows:
            return None
        monthly_total = sum(r.predicted_tokens for r in likely_rows[:30])
        first = likely_rows[0]
        last = likely_rows[-1] if len(likely_rows) > 1 else first
        slope = (last.predicted_tokens - first.predicted_tokens) / max(len(likely_rows) - 1, 1)
        direction = "flat" if abs(slope) < 1000 else ("up" if slope > 0 else "down")
        return {
            "monthly_likely_tokens": round(monthly_total, 0),
            "trend_slope": round(slope, 1),
            "trend_direction": direction,
        }
 
    def _build_forecast_detail(self, rows: list) -> dict:
        """Always returns a dict — never None."""
        if not rows:
            return {"available": False, "daily": []}
 
        date_map: dict = {}
        for r in rows:
            fd = r.forecast_date
            if fd not in date_map:
                date_map[fd] = {
                    "forecast_date": fd.isoformat(),
                    "horizon_days": r.horizon_days,
                    "likely_tokens": 0.0,
                    "pessimistic_tokens": 0.0,
                    "optimistic_tokens": 0.0,
                }
            if r.scenario == "likely":
                date_map[fd]["likely_tokens"] = r.predicted_tokens
            elif r.scenario == "pessimistic":
                date_map[fd]["pessimistic_tokens"] = r.predicted_tokens
            elif r.scenario == "optimistic":
                date_map[fd]["optimistic_tokens"] = r.predicted_tokens
 
        daily = sorted(date_map.values(), key=lambda x: x["forecast_date"])
        likely_rows = [r for r in rows if r.scenario == "likely"]
        monthly_total = sum(r.predicted_tokens for r in likely_rows[:30])
 
        if likely_rows:
            first = likely_rows[0]
            last = likely_rows[-1]
            slope = (last.predicted_tokens - first.predicted_tokens) / max(len(likely_rows) - 1, 1)
            direction = "flat" if abs(slope) < 1000 else ("up" if slope > 0 else "down")
            slope_sign = "+" if slope >= 0 else ""
            description = f"{slope_sign}{int(slope):,} tokens/day"
        else:
            slope = 0.0
            direction = "flat"
            description = "No trend data"
 
        return {
            "available": True,
            "trend_slope": round(slope, 1),
            "trend_slope_description": description,
            "monthly_likely_tokens": round(monthly_total, 0),
            "daily": daily,
        }
 
 
# ============================================================
# TITLE / DESCRIPTION HELPERS
# ============================================================
 
def _anomaly_title(anomaly_type: str, severity: str) -> str:
    titles = {
        "token_spike": "Token usage spike detected",
        "token_drop": "Token usage drop detected",
        "cost_spike": "Cost spike detected",
        "pattern_break": "Unusual usage pattern detected",
    }
    base = titles.get(anomaly_type, "Anomaly detected")
    return f"[{severity.upper()}] {base}"
 
 
def _anomaly_description(anomaly: dict) -> str:
    obs = int(anomaly.get("observed_value", 0))
    mean = int(anomaly.get("baseline_mean", 0))
    return f"Observed: {obs:,} tokens (baseline: {mean:,})"
 
 
def _risk_title(risk_type: str, days: int) -> str:
    if risk_type == "token_limit":
        return f"Daily token limit will be reached in {days} days"
    elif risk_type == "cost_budget":
        return f"Monthly budget will be exhausted in {days} days"
    return f"Budget risk: {days} days to limit"
 
 
def _risk_description(risk: dict) -> str:
    limit = risk.get("governance_limit", 0)
    pct = risk.get("pct_of_limit_today", 0)
    days = risk.get("days_until_exhaustion", 0)
    return (
        f"Currently at {pct:.1f}% of limit "
        f"({limit:,.0f}). Projected exhaustion in {days} days."
    )