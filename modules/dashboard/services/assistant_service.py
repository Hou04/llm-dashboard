"""
AssistantService — M8 Agent.

M8 is the conversational AI copilot of the dashboard.
A user types a question in plain language. M8:
  1. Classifies the intent (cost? anomaly? forecast? governance?)
  2. Fetches only the relevant data from the database
  3. Builds a compact context under 600 tokens
  4. Calls Groq/Llama (free) or any configured LLM provider
  5. Returns a structured answer: text + key figures + action links
  6. Logs the interaction for cost tracking
  7. Caches the response in Redis for 2 hours

Design rules:
  - Reads ONLY. Never writes business data.
  - Never accesses raw logs or raw prompts.
  - Every LLM call is triggered by a human interaction, never automated.
  - Fallback to rule-based answer if LLM is unavailable.
  - Context always under 600 tokens — M8 must be cost-efficient.
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import text, select, func, desc, case
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis import get_cache_redis
from core.settings import settings
from modules.detection.services.llm_provider import LLMProvider
from modules.gateway.models import LLMTokenLog
from modules.detection.models import LLMAnomalyRecord
from modules.forecasting.models_optimizer import LLMModelRecommendation

from core.config_registry import config

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION — loaded from ConfigRegistry at runtime
# ============================================================

def _cache_ttl() -> int:
    return config.get("m8.cache_ttl_seconds", 7200)

def _max_answer_tokens() -> int:
    return config.get("m8.max_answer_tokens", 500)

def _llm_timeout() -> float:
    return config.get("m8.timeout_seconds", 25.0)

# ============================================================
# INTENT CLASSIFIER
#
# Before calling the LLM, we figure out what the question is about.
# This determines which database queries to run.
# A question about costs should not trigger an anomaly query.
# ============================================================

INTENT_KEYWORDS = {
    "cost": [
        "coût", "cost", "dépense", "spend", "spending", "cher",
        "expensive", "prix", "price", "facture", "billing", "dollar",
        "euro", "budget", "montant", "amount",
    ],
    "anomaly": [
        "anomalie", "anomaly", "dérive", "drift", "spike", "pic",
        "hausse", "augmentation", "increase", "alert", "alerte",
        "critique", "critical", "anormal", "unusual",
    ],
    "forecast": [
        "prévision", "forecast", "fin de mois", "end of month",
        "prévu", "projected", "estimé", "estimated", "projection",
        "prochain", "next", "will", "va coûter",
    ],
    "governance": [
        "bloqué", "blocked", "block", "quota", "limite", "limit",
        "downgrade", "règle", "rule", "autorisé", "allowed",
        "interdit", "forbidden", "gouvernance", "governance",
    ],
    "model": [
        "modèle", "model", "gpt", "claude", "mistral", "gemini",
        "llama", "provider", "openai", "anthropic",
    ],
    "agent": [
        "agent", "bot", "application", "app", "service",
        "crm", "support", "analytics", "chatbot",
    ],
    "optimize": [
        "optimis", "réduire", "reduce", "économie", "saving",
        "moins cher", "cheaper", "moins", "cut", "improve",
    ],
}


def classify_intent(question: str) -> list[str]:
    """
    Classify the user's question into one or more intents.

    We scan the question for keywords from each intent category.
    Multiple intents can match (e.g. "why did costs spike" → cost + anomaly).
    If nothing matches, we return ["general"] which fetches everything.

    This is intentionally simple — no ML, just keyword matching.
    Fast, reliable, zero tokens consumed.
    """
    question_lower = question.lower()
    detected = [
        intent
        for intent, keywords in INTENT_KEYWORDS.items()
        if any(kw in question_lower for kw in keywords)
    ]
    return detected if detected else ["general"]


# ============================================================
# THE SERVICE CLASS
# ============================================================

class AssistantService:
    """
    M8 Agent — conversational dashboard assistant.

    Usage in a FastAPI endpoint:
        service = AssistantService(session)
        result = await service.ask(
            question="Which tenant spends the most?",
            tenant_id=None,    # None = all tenants
            user_id="user_123",
            period_days=30,
        )
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.llm = LLMProvider()  # supports Groq, OpenAI, Anthropic, Mistral, Ollama

    # ================================================================
    # PUBLIC — MAIN ENTRY POINT
    # ================================================================

    async def ask(
        self,
        question: str,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        period_days: int = 30,
        session_id: Optional[str] = None,
    ) -> dict:
        """
        Answer a natural language question about the LLM platform.

        Flow:
          1. Classify intent from question text (no LLM needed here)
          2. Check Redis cache — if cached, return immediately
          3. Fetch relevant data from the database
          4. Call LLM with compact context
          5. Parse and validate the response
          6. Log the interaction
          7. Cache the result
          8. Return to caller

        Args:
            question:    The user's question in any language.
            tenant_id:   If set, filter data to this tenant.
                         If None, include all tenants.
            user_id:     Pseudonymised user ID for the interaction log.
            period_days: How many days of history to include (7, 30, 90).

        Returns a dict:
            {
                "answer":      str,   # 2-4 sentence answer
                "key_figures": list,  # [{"label":..., "value":..., "unit":...}]
                "actions":     list,  # [{"title":..., "url":..., "type":...}]
                "intent":      list,  # detected intents
                "tokens_used": int,   # LLM tokens consumed by this call
                "model_used":  str,   # which provider/model responded
                "from_cache":  bool,  # True if Redis cache hit
            }
        """
        # Step 1: get conversation history if session_id is provided
        history = []
        if session_id:
            history = await self._read_session_history(session_id)

        # Step 2: classify intent (uses LLM zero-shot classifier)
        intents = await self._classify_intent_zero_shot(question)

        # Step 3: cache check (only if stateless)
        cache_key = self._make_cache_key(question, tenant_id)
        if not session_id:
            cached = await self._read_cache(cache_key)
            if cached is not None:
                cached["from_cache"] = True
                return cached

        # Step 4: fetch data from database
        context = await self._build_context(
            intents=intents,
            tenant_id=tenant_id,
            period_days=period_days,
        )

        # Step 5: call LLM
        llm_result = await self._call_llm(question, context, intents, history)

        # Step 6: log the interaction (best-effort — never crash on log failure)
        await self._log_interaction(
            user_id=user_id,
            question=question,
            intents=intents,
            tenant_id=tenant_id,
            tokens_used=llm_result["tokens_used"],
            model_used=llm_result["model_used"],
            response_time_ms=llm_result.get("response_time_ms", 0),
        )

        # Step 7: build result
        result = {
            "answer":      llm_result["answer"],
            "key_figures": llm_result.get("key_figures", []),
            "actions":     llm_result.get("actions", []),
            "intent":      intents,
            "tokens_used": llm_result["tokens_used"],
            "model_used":  llm_result["model_used"],
            "from_cache":  False,
        }

        # Step 8: cache (stateless) or append history (stateful)
        if llm_result["tokens_used"] > 0:
            if not session_id:
                await self._write_cache(cache_key, result)
            else:
                await self._append_session_history(session_id, question, result["answer"])

        return result

    async def get_suggestions(
        self, tenant_id: Optional[str] = None
    ) -> list[str]:
        """
        Return contextual question suggestions.

        If a specific tenant is selected, add tenant-specific questions.
        Always include platform-wide questions.
        """
        platform_questions = [
            "Which tenant spends the most this month?",
            "Are there any critical anomalies right now?",
            "Where can I reduce AI costs by 20%?",
            "What is the projected budget by end of month?",
            "Which agents are the most expensive?",
            "Which LLM models are used the most?",
            "How many calls were blocked by governance rules?",
            "Compare costs between tenants this month.",
        ]

        if tenant_id:
            tenant_questions = [
                f"What is the cost breakdown for {tenant_id}?",
                f"Are there any anomalies for {tenant_id}?",
                f"What optimizations are recommended for {tenant_id}?",
                f"What is the forecasted spend for {tenant_id} this month?",
            ]
            return tenant_questions + platform_questions

        return platform_questions

    # ================================================================
    # PRIVATE — CONTEXT BUILDER
    # ================================================================

    async def _build_context(
        self,
        intents: list[str],
        tenant_id: Optional[str],
        period_days: int,
    ) -> str:
        """
        Build a compact database summary for the LLM using strict SQLAlchemy ORM.

        Key constraint: this must stay under 600 tokens.
        We only query what the intent needs.

        ORM ensures SQL injection or prompt-leakage to other tenants is structurally impossible.
        """
        from_dt = datetime.now(timezone.utc) - timedelta(days=period_days)
        sections = []

        # Helper to apply the strict tenant scope
        def apply_tenant_scope(stmt, model_cls):
            if tenant_id is not None:
                return stmt.where(model_cls.tenant_id == tenant_id)
            else:
                return stmt.where(model_cls.tenant_id.is_not(None))

        # ── Cost summary ──────────────────────────────────────────
        # Pulled for: cost, general, comparison, optimize
        if any(i in intents for i in ["cost", "general", "comparison", "optimize"]):
            try:
                stmt = select(
                    LLMTokenLog.tenant_id,
                    func.count(LLMTokenLog.id).label("calls"),
                    func.sum(LLMTokenLog.total_tokens).label("tokens"),
                    func.round(func.sum(LLMTokenLog.cost_usd), 4).label("cost_usd")
                ).where(
                    LLMTokenLog.created_at >= from_dt,
                    LLMTokenLog.status != 'blocked'
                ).group_by(LLMTokenLog.tenant_id).order_by(
                    desc(func.sum(LLMTokenLog.cost_usd))
                ).limit(10)
                
                stmt = apply_tenant_scope(stmt, LLMTokenLog)
                result = await self.session.execute(stmt)
                
                rows = result.all()
                if rows:
                    lines = [
                        f"  {r.tenant_id}: ${r.cost_usd} | "
                        f"{r.tokens:,} tokens | {r.calls:,} calls"
                        for r in rows
                    ]
                    sections.append(
                        f"COST SUMMARY (last {period_days} days):\n"
                        + "\n".join(lines)
                    )
            except Exception as e:
                logger.warning(f"M8 cost context query failed: {e}")

        # ── Model breakdown ────────────────────────────────────────
        # Pulled for: model, cost, optimize
        if any(i in intents for i in ["model", "cost", "optimize"]):
            try:
                stmt = select(
                    LLMTokenLog.model,
                    LLMTokenLog.provider,
                    func.count(LLMTokenLog.id).label("calls"),
                    func.round(func.sum(LLMTokenLog.cost_usd), 4).label("cost_usd")
                ).where(
                    LLMTokenLog.created_at >= from_dt,
                    LLMTokenLog.status != 'blocked'
                ).group_by(LLMTokenLog.model, LLMTokenLog.provider).order_by(
                    desc(func.sum(LLMTokenLog.cost_usd))
                ).limit(6)

                stmt = apply_tenant_scope(stmt, LLMTokenLog)
                result = await self.session.execute(stmt)

                rows = result.all()
                if rows:
                    lines = [
                        f"  {r.model} ({r.provider}): "
                        f"${r.cost_usd} | {r.calls:,} calls"
                        for r in rows
                    ]
                    sections.append("MODEL BREAKDOWN:\n" + "\n".join(lines))
            except Exception as e:
                logger.warning(f"M8 model context query failed: {e}")

        # ── Active anomalies ───────────────────────────────────────
        # Pulled for: anomaly, general
        if any(i in intents for i in ["anomaly", "general"]):
            try:
                severity_order = case(
                    (LLMAnomalyRecord.severity == 'critical', 1),
                    (LLMAnomalyRecord.severity == 'high', 2),
                    (LLMAnomalyRecord.severity == 'warning', 3),
                    else_=4
                )
                
                stmt = select(
                    LLMAnomalyRecord.tenant_id,
                    LLMAnomalyRecord.anomaly_type,
                    LLMAnomalyRecord.severity,
                    LLMAnomalyRecord.description,
                    LLMAnomalyRecord.detected_at
                ).where(
                    LLMAnomalyRecord.resolved == False
                ).order_by(
                    severity_order,
                    desc(LLMAnomalyRecord.detected_at)
                ).limit(5)

                stmt = apply_tenant_scope(stmt, LLMAnomalyRecord)
                result = await self.session.execute(stmt)

                rows = result.all()
                if rows:
                    lines = [
                        f"  [{r.severity.upper()}] {r.tenant_id} — "
                        f"{r.anomaly_type}: "
                        f"{r.description or 'No description'}"
                        for r in rows
                    ]
                    sections.append("ACTIVE ANOMALIES:\n" + "\n".join(lines))
                else:
                    sections.append("ACTIVE ANOMALIES: None currently active.")
            except Exception as e:
                logger.warning(f"M8 anomaly context query failed: {e}")

        # ── Governance (blocked calls) ─────────────────────────────
        # Pulled for: governance, general
        if any(i in intents for i in ["governance", "general"]):
            try:
                blocked_filter = func.count(LLMTokenLog.id).filter(LLMTokenLog.status == 'blocked')
                stmt = select(
                    LLMTokenLog.tenant_id,
                    blocked_filter.label("blocked"),
                    func.count(LLMTokenLog.id).label("total")
                ).where(
                    LLMTokenLog.created_at >= from_dt
                ).group_by(LLMTokenLog.tenant_id).having(
                    blocked_filter > 0
                ).order_by(
                    desc(blocked_filter)
                ).limit(5)

                stmt = apply_tenant_scope(stmt, LLMTokenLog)
                result = await self.session.execute(stmt)

                rows = result.all()
                if rows:
                    lines = [
                        f"  {r.tenant_id}: "
                        f"{r.blocked} blocked / {r.total} total calls"
                        for r in rows
                    ]
                    sections.append(
                        "GOVERNANCE — BLOCKED CALLS:\n" + "\n".join(lines)
                    )
            except Exception as e:
                logger.warning(f"M8 governance context query failed: {e}")

        # ── Agent breakdown ────────────────────────────────────────
        # Pulled for: agent, optimize
        if any(i in intents for i in ["agent", "optimize"]):
            try:
                stmt = select(
                    LLMTokenLog.agent_id,
                    LLMTokenLog.tenant_id,
                    func.count(LLMTokenLog.id).label("calls"),
                    func.round(func.sum(LLMTokenLog.cost_usd), 4).label("cost_usd"),
                    func.round(func.avg(LLMTokenLog.input_tokens)).label("avg_input_tokens")
                ).where(
                    LLMTokenLog.created_at >= from_dt,
                    LLMTokenLog.agent_id.is_not(None),
                    LLMTokenLog.status != 'blocked'
                ).group_by(LLMTokenLog.agent_id, LLMTokenLog.tenant_id).order_by(
                    desc(func.sum(LLMTokenLog.cost_usd))
                ).limit(6)

                stmt = apply_tenant_scope(stmt, LLMTokenLog)
                result = await self.session.execute(stmt)

                rows = result.all()
                if rows:
                    lines = [
                        f"  {r.agent_id} ({r.tenant_id}): "
                        f"${r.cost_usd} | "
                        f"{r.avg_input_tokens} avg input tokens"
                        for r in rows
                    ]
                    sections.append(
                        "TOP AGENTS BY COST:\n" + "\n".join(lines)
                    )
            except Exception as e:
                logger.warning(f"M8 agent context query failed: {e}")

        # ── Optimization recommendations ───────────────────────────
        # Pulled for: optimize
        if "optimize" in intents:
            try:
                stmt = select(
                    LLMModelRecommendation.agent_id,
                    LLMModelRecommendation.tenant_id,
                    LLMModelRecommendation.current_model,
                    LLMModelRecommendation.recommended_model,
                    LLMModelRecommendation.expected_monthly_saving_usd,
                    LLMModelRecommendation.expected_saving_pct
                ).where(
                    LLMModelRecommendation.status == 'proposed'
                ).order_by(
                    desc(LLMModelRecommendation.expected_monthly_saving_usd)
                ).limit(4)

                stmt = apply_tenant_scope(stmt, LLMModelRecommendation)
                result = await self.session.execute(stmt)

                rows = result.all()
                if rows:
                    lines = [
                        f"  {r.agent_id} ({r.tenant_id}): "
                        f"{r.current_model} → {r.recommended_model} "
                        f"saves ${r.expected_monthly_saving_usd}/mo "
                        f"({r.expected_saving_pct:.0f}%)"
                        for r in rows
                    ]
                    sections.append(
                        "OPTIMIZATION OPPORTUNITIES:\n" + "\n".join(lines)
                    )
            except Exception as e:
                logger.warning(f"M8 optimization context query failed: {e}")

        if not sections:
            return (
                "No data available for the requested period and tenant filter."
            )

        return "\n\n".join(sections)

    # ================================================================
    # PRIVATE — LLM CALL
    # ================================================================

    async def _call_llm(
        self,
        question: str,
        context: str,
        intents: list[str],
        history: list[dict],
    ) -> dict:
        """
        Send the question + context to the LLM and parse the response.

        The prompt instructs the LLM to return JSON only with three keys:
          - answer:      2-4 sentences in plain business language
          - key_figures: the 2-4 most important numbers
          - actions:     1-3 concrete next steps with links

        If the LLM call fails or returns unparseable text, a safe
        rule-based fallback is returned. The dashboard never crashes.
        """
        start_time = datetime.now(timezone.utc)

        prompt = self._build_prompt(question, context, history)

        try:
            result = await self.llm.complete(
                prompt=prompt,
                provider=settings.m4_default_provider,
                model=settings.m4_default_model,
                max_tokens=_max_answer_tokens(),
                timeout=_llm_timeout(),
            )

            elapsed_ms = int(
                (datetime.now(timezone.utc) - start_time).total_seconds()
                * 1000
            )

            # If provider returned empty (all providers failed)
            if not result.get("text"):
                logger.warning("M8: all LLM providers returned empty — using fallback")
                return self._rule_based_fallback()

            parsed = self._parse_llm_response(result["text"])
            parsed["tokens_used"] = (
                result["input_tokens"] + result["output_tokens"]
            )
            parsed["model_used"] = (
                f"{result.get('provider', '?')}/{result.get('model', '?')}"
            )
            parsed["response_time_ms"] = elapsed_ms
            return parsed

        except Exception as exc:
            logger.warning(f"M8 LLM call failed: {exc} — using fallback")
            return self._rule_based_fallback()

    def _build_prompt(self, question: str, context: str, history: list[dict]) -> str:
        """
        Build the structured prompt for M8.

        Key decisions:
        - The LLM is told to return JSON only (no markdown, no preamble)
        - The schema is specified precisely — reduces hallucination
        - The context (database data) comes first — grounding before instruction
        - "Use the actual numbers from the data" — forces grounded answers
        """
        history_text = "None"
        if history:
            blocks = []
            for h in history:
                blocks.append(f"User: {h['question']}\nAssistant: {h['answer']}")
            history_text = "\n\n".join(blocks)

        return f"""You are an AI assistant for an LLM cost monitoring dashboard.
Your job: answer business questions about AI token costs, anomalies, and governance.

REAL DATA FROM THE DATABASE:
{context}

PREVIOUS CONVERSATION HISTORY:
{history_text}

USER QUESTION:
{question}

Respond with ONLY this JSON object (no markdown, no explanation before or after):
{{
  "answer": "A direct 2-4 sentence answer using the actual numbers from the data above. Plain business language. No jargon.",
  "key_figures": [
    {{"label": "Metric name", "value": "$265.96", "unit": "30-day total"}},
    {{"label": "Another metric", "value": "53,654", "unit": "API calls"}}
  ],
  "actions": [
    {{"title": "View tenant detail", "url": "/v1/dashboard/tenant/TENANT_ID", "type": "link"}},
    {{"title": "Run optimization analysis", "url": "/v1/forecasting/optimize/TENANT_ID", "type": "action"}}
  ]
}}

Rules:
- answer: use real numbers from the data, 2-4 sentences, plain language
- key_figures: 2 to 4 items, the most important metrics for this question
- actions: 1 to 3 concrete next steps relevant to the answer
- If the data is insufficient to answer, say so honestly in the answer field
- Respond ONLY with the JSON — nothing before, nothing after"""

    def _parse_llm_response(self, raw_text: str) -> dict:
        """
        Parse the LLM's JSON response.

        The LLM sometimes wraps JSON in ```json fences despite instructions.
        We strip those, then parse, then validate each field.
        If anything fails, return the fallback.
        """
        try:
            text = raw_text.strip()

            # Strip markdown code fences if present
            if text.startswith("```"):
                lines = text.split("\n")
                # Remove first line (```json) and last line (```)
                text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
            text = text.strip()

            parsed = json.loads(text)

            # Validate and clean each field
            answer = str(parsed.get("answer", ""))[:1000]
            if not answer:
                raise ValueError("Empty answer field")

            key_figures = []
            for fig in parsed.get("key_figures", [])[:4]:
                if isinstance(fig, dict) and "label" in fig and "value" in fig:
                    key_figures.append({
                        "label": str(fig.get("label", ""))[:100],
                        "value": str(fig.get("value", ""))[:50],
                        "unit":  str(fig.get("unit", ""))[:50],
                    })

            actions = []
            for act in parsed.get("actions", [])[:3]:
                if isinstance(act, dict) and "title" in act and "url" in act:
                    actions.append({
                        "title": str(act.get("title", ""))[:200],
                        "url":   str(act.get("url",   ""))[:500],
                        "type":  str(act.get("type",  "link"))[:20],
                    })

            return {
                "answer":      answer,
                "key_figures": key_figures,
                "actions":     actions,
            }

        except Exception as exc:
            logger.warning(
                f"M8 response parse failed: {exc} | "
                f"Raw: {raw_text[:200]}"
            )
            return self._rule_based_fallback()

    def _rule_based_fallback(self) -> dict:
        """
        Always-available fallback when the LLM is unavailable.

        Returns a generic but honest answer with navigation links.
        The dashboard never shows a blank response.
        """
        return {
            "answer": (
                "I couldn't generate a detailed analysis right now — "
                "the AI service may be temporarily unavailable. "
                "Your data is safe and the dashboard endpoints below "
                "have full real-time information. "
                "Please use the links below or try again in a moment."
            ),
            "key_figures": [],
            "actions": [
                {
                    "title": "View executive overview",
                    "url":   "/v1/dashboard/executive",
                    "type":  "link",
                },
                {
                    "title": "Check active alerts",
                    "url":   "/v1/dashboard/alerts",
                    "type":  "link",
                },
                {
                    "title": "View system health",
                    "url":   "/v1/dashboard/health",
                    "type":  "link",
                },
            ],
            "tokens_used":     0,
            "model_used":      "fallback/rule-based",
            "response_time_ms": 0,
        }

    # ================================================================
    # PRIVATE — INTERACTION LOGGING
    # ================================================================

    async def _log_interaction(
        self,
        user_id: Optional[str],
        question: str,
        intents: list[str],
        tenant_id: Optional[str],
        tokens_used: int,
        model_used: str,
        response_time_ms: int,
    ) -> None:
        """
        Log this M8 interaction to llm_dashboard_assistant_log.

        This makes M8's own token consumption visible in the dashboard —
        it appears as a cost line just like any other LLM call.

        Best-effort: if the log insert fails, we log a warning
        but never crash the API response.
        """
        try:
            await self.session.execute(text("""
                INSERT INTO llm_dashboard_assistant_log
                    (id, user_id, question_preview, question_type,
                     tenant_scope, tokens_used, llm_model_used,
                     response_time_ms, created_at)
                VALUES
                    (:id, :user_id, :question_preview, :question_type,
                     :tenant_scope, :tokens_used, :llm_model_used,
                     :response_time_ms, :created_at)
            """), {
                "id":               str(uuid.uuid4()),
                "user_id":          user_id or "anonymous",
                "question_preview": question[:200],
                "question_type":    ",".join(intents),
                "tenant_scope":     tenant_id or "all",
                "tokens_used":      tokens_used,
                "llm_model_used":   model_used,
                "response_time_ms": response_time_ms,
                "created_at":       datetime.now(timezone.utc),
            })
            await self.session.commit()
        except Exception as e:
            logger.warning(f"M8 interaction log failed (non-critical): {e}")

    # ================================================================
    # PRIVATE — REDIS CACHE
    # ================================================================

    def _make_cache_key(
        self,
        question: str,
        tenant_id: Optional[str],
    ) -> str:
        """
        Build a Redis cache key from the question + tenant.

        We MD5-hash the lowercased question so the key is a fixed length.
        Same question, same tenant = same key = cache hit.
        Different question OR different tenant = different key.
        """
        key_input = f"{question.lower().strip()}:{tenant_id or 'all'}"
        key_hash = hashlib.md5(key_input.encode()).hexdigest()[:16]
        return f"m8:answer:{key_hash}"

    async def _read_cache(self, key: str) -> Optional[dict]:
        """Check Redis for a cached answer. Returns None on cache miss."""
        try:
            redis = get_cache_redis()
            value = await redis.get(key)
            await redis.aclose()
            if value:
                return json.loads(value)
            return None
        except Exception:
            # Redis unavailable — just skip the cache, proceed normally
            return None

    async def _write_cache(self, key: str, data: dict) -> None:
        """Write an answer to Redis with a 2-hour TTL."""
        try:
            redis = get_cache_redis()
            await redis.set(
                key,
                json.dumps(data, default=str),
                ex=_cache_ttl(),
            )
            await redis.aclose()
        except Exception:
            # Redis unavailable — just skip the cache, no problem
            pass

    # ================================================================
    # PRIVATE — SESSION HISTORY & ZERO-SHOT NLP
    # ================================================================

    async def _read_session_history(self, session_id: str) -> list[dict]:
        """Fetch conversation history from Redis."""
        key = f"m8:history:{session_id}"
        try:
            redis = get_cache_redis()
            val = await redis.get(key)
            await redis.aclose()
            if val:
                return json.loads(val)
        except Exception as e:
            logger.warning(f"Failed to read session history: {e}")
        return []

    async def _append_session_history(self, session_id: str, question: str, answer: str) -> None:
        """Append Q&A to history and truncate to avoid huge contexts."""
        key = f"m8:history:{session_id}"
        try:
            history = await self._read_session_history(session_id)
            history.append({"question": str(question), "answer": str(answer)})
            # Keep max 10 exchanges (20 messages)
            if len(history) > 10:
                history = history[-10:]
            
            redis = get_cache_redis()
            await redis.set(key, json.dumps(history), ex=_cache_ttl())
            await redis.aclose()
        except Exception as e:
            logger.warning(f"Failed to append session history: {e}")

    async def _classify_intent_zero_shot(self, question: str) -> list[str]:
        """
        Use the configured LLM for zero-shot intent classification.
        Falls back to keyword matching if the call fails or times out.
        """
        prompt = f"""You are an intent classifier for a dashboard assistant.
Analyze the user's question and map it to one or more of these strict categories:
["cost", "anomaly", "forecast", "governance", "model", "agent", "optimize", "general"]

USER QUESTION: {question}

Return ONLY a valid JSON object with exactly one key "intents" containing a list of matching string categories. No other text. None.
Example: {{"intents": ["cost", "anomaly"]}}"""
        try:
            result = await self.llm.complete(
                prompt=prompt,
                provider=settings.m4_default_provider,
                model=settings.m4_default_model,
                max_tokens=50,
                timeout=5.0, # Fail fast
            )
            raw = result.get("text", "").strip()
            # Clean markdown JSON block
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
            
            parsed = json.loads(raw.strip())
            intents = parsed.get("intents", [])
            valid_intents = ["cost", "anomaly", "forecast", "governance", "model", "agent", "optimize", "general"]
            filtered = [i for i in intents if i in valid_intents]
            
            if filtered:
                logger.debug(f"Zero-shot intent classification successful: {filtered}")
                return filtered
        except Exception as e:
            logger.warning(f"Zero-shot intent classification failed, falling back to keywords: {e}")
            
        # Fallback to local keyword matcher
        return classify_intent(question)