"""
OptimizerService — M5 Agent.

M5 is the efficiency engineer of the AI system.
It analyzes how each agent uses LLM APIs and recommends:
1. Structural optimizations (reduce verbose prompts, add output format)
2. Model substitutions (use cheaper model where complexity doesn't justify cost)
3. LLM-assisted prompt rewriting via Groq/Llama (free, Step 4 of spec)

CLASSIFIER DESIGN:
Model recommendation uses a rule-based classifier, not ML.

Rationale: With 5 agents and 30 days of data, a learned classifier
would overfit. Rule-based with explicit thresholds is more reliable,
more interpretable, and auditable — exactly what a governance system needs.

The classification features are:
- avg_input_tokens: how complex are the inputs?
- avg_output_tokens: how much generation is required?
- error_rate_pct: is the current model struggling?
- avg_total_tokens: overall call size

Classification rules (conservative — prefer false negatives over false positives):
  DOWNGRADE to mini if ALL of:
    avg_input < 400 tokens (simple context)
    avg_output < 500 tokens (moderate generation)
    error_rate < 5% (model is not struggling)
    currently using gpt-4o or claude-3-opus

  UPGRADE to gpt-4o if ANY of:
    avg_input > 1500 tokens (complex context requiring reasoning)
    avg_output > 1200 tokens (heavy generation)
    error_rate > 10% (model may be too weak)
    currently using gpt-4o-mini with high error rate

ROI SIMULATION:
For every model change recommendation:
  current_monthly_cost = avg_cost_per_call × monthly_call_count
  projected_cost = same volume × new_model_price_ratio × avg_total_tokens
  saving = current - projected
  saving_pct = saving / current × 100

MODEL PRICING (USD per 1k tokens, approximate):
  gpt-4o:         input 0.0025, output 0.010
  gpt-4o-mini:    input 0.00015, output 0.0006
  claude-3-haiku: input 0.00025, output 0.00125
  claude-sonnet:  input 0.003, output 0.015
"""

import json
import uuid
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from modules.detection.services.llm_provider import LLMProvider
from modules.forecasting.repositories.optimizer_repository import OptimizerRepository

from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.cluster import KMeans
import numpy as np

logger = logging.getLogger(__name__)

_embed_model = None

def get_embed_model():
    global _embed_model
    if _embed_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embed_model = SentenceTransformer('all-MiniLM-L6-v2')
        except ImportError:
            pass
    return _embed_model

# ============================================================
# MODEL PRICING TABLE (USD per 1k tokens)
# ============================================================
MODEL_PRICING = {
    "gpt-4o":           {"input": 0.0025,  "output": 0.010},
    "gpt-4o-mini":      {"input": 0.00015, "output": 0.0006},
    "claude-3-haiku":   {"input": 0.00025, "output": 0.00125},
    "claude-3-sonnet":  {"input": 0.003,   "output": 0.015},
    "claude-3-opus":    {"input": 0.015,   "output": 0.075},
}

# Model tier hierarchy (higher index = more capable = more expensive)
MODEL_TIERS = {
    "gpt-4o-mini":    1,
    "claude-3-haiku": 1,
    "gpt-4o":         3,
    "claude-3-sonnet":3,
    "claude-3-opus":  5,
}

# Downgrade candidates: expensive → cheaper alternative
DOWNGRADE_MAP = {
    "gpt-4o":          "gpt-4o-mini",
    "claude-3-sonnet": "claude-3-haiku",
    "claude-3-opus":   "claude-3-haiku",
}

# Upgrade candidates: cheap → more capable
UPGRADE_MAP = {
    "gpt-4o-mini":    "gpt-4o",
    "claude-3-haiku": "claude-3-sonnet",
}

# Thresholds for downgrade eligibility
DOWNGRADE_MAX_INPUT_TOKENS  = 400
DOWNGRADE_MAX_OUTPUT_TOKENS = 500
DOWNGRADE_MAX_ERROR_RATE    = 5.0

# Thresholds for upgrade recommendation
UPGRADE_MIN_INPUT_TOKENS  = 1500
UPGRADE_MIN_OUTPUT_TOKENS = 1200
UPGRADE_MIN_ERROR_RATE    = 10.0

# Minimum monthly saving to bother recommending (USD)
MIN_SAVING_THRESHOLD_USD = 0.50

# Prompt optimization thresholds
VERBOSE_INPUT_THRESHOLD  = 800   # avg_input > this → verbose prompt candidate
MISSING_FORMAT_THRESHOLD = 600   # avg_output > this → missing output format candidate


class OptimizerService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo    = OptimizerRepository(session)
        self.llm     = LLMProvider()   # multi-provider: Groq, OpenAI, Anthropic, Ollama…

    # ================================================================
    # PUBLIC — FULL OPTIMIZATION PIPELINE
    # ================================================================

    async def run_optimization_for_tenant(
        self, tenant_id: str, lookback_days: int = 30
    ) -> dict:
        """
        Run M5 full optimization analysis for one tenant.

        Fetches agent profiles, classifies each agent, generates
        model recommendations and prompt optimization suggestions,
        persists all records, and returns a summary.
        """
        profiles = await self.repo.get_agent_profiles(tenant_id, lookback_days)

        if not profiles:
            logger.info(
                "No agent data for optimization",
                extra={"tenant_id": tenant_id},
            )
            return {
                "tenant_id": tenant_id,
                "agents_analyzed": 0,
                "model_recommendations": [],
                "prompt_optimizations": [],
                "total_projected_monthly_saving_usd": "0.0000",
            }
            
        # --- Phase 2: KMeans Use Case Segmentation ---
        n_clusters = min(3, len(profiles))
        if n_clusters > 1:
            try:
                X_clust = np.array([[p["avg_input_tokens"], p["avg_output_tokens"], p["error_rate_pct"]] for p in profiles])
                kmeans = KMeans(n_clusters=n_clusters, random_state=42)
                labels = kmeans.fit_predict(X_clust)
                for i, p in enumerate(profiles):
                    p["use_case"] = f"Cluster {labels[i]+1} (Avg {int(p['avg_input_tokens'])}tk)"
            except Exception as e:
                logger.warning(f"KMeans segmentation failed: {e}")
                for p in profiles: p["use_case"] = "general"
        else:
            for p in profiles: p["use_case"] = "general"

        # --- Phase 2: Train Local DecisionTree for Models ---
        clf = None
        tree_rules = "No machine learning rules applied due to low variance."
        try:
            X_tree, y_tree = [], []
            for p in profiles:
                if p["avg_input_tokens"] < 400 and p["error_rate_pct"] < 5.0:
                    y_tree.append(0) # downgrade
                elif p["error_rate_pct"] > 10.0:
                    y_tree.append(2) # upgrade
                else:
                    y_tree.append(1) # keep
                X_tree.append([p["avg_input_tokens"], p["avg_output_tokens"], p["error_rate_pct"]])
            
            if len(set(y_tree)) > 1:
                clf = DecisionTreeClassifier(max_depth=3, random_state=42)
                clf.fit(X_tree, y_tree)
                tree_rules = export_text(clf, feature_names=["avg_input", "avg_output", "error_rate"])
        except Exception as e:
            logger.warning(f"DecisionTree classification failed: {e}")

        model_recs  = []
        prompt_opts = []

        for profile in profiles:
            # Model recommendation uses the ML classifier dynamically
            model_rec = self._classify_model(tenant_id, profile, clf, tree_rules)
            if model_rec is not None:
                stored = await self.repo.upsert_model_recommendation(
                    tenant_id, model_rec
                )
                model_recs.append(stored)

            # Prompt structure optimizations (statistical pattern detection)
            opt_list = self._analyze_prompt_patterns(tenant_id, profile)
            for opt_data in opt_list:
                stored_opt = await self.repo.create_prompt_optimization(opt_data)
                prompt_opts.append(stored_opt)

        await self.session.commit()

        total_saving = sum(
            float(r.expected_monthly_saving_usd) for r in model_recs
        )

        logger.info(
            "M5 optimization complete",
            extra={
                "tenant_id":      tenant_id,
                "agents":         len(profiles),
                "model_recs":     len(model_recs),
                "prompt_opts":    len(prompt_opts),
                "monthly_saving": round(total_saving, 4),
            },
        )

        return {
            "tenant_id":                         tenant_id,
            "agents_analyzed":                   len(profiles),
            "model_recommendations":             model_recs,
            "prompt_optimizations":              prompt_opts,
            "total_projected_monthly_saving_usd": str(round(total_saving, 4)),
        }

    async def get_model_recommendations(self, tenant_id: str) -> list:
        return await self.repo.get_model_recommendations(tenant_id)

    async def get_all_model_recommendations(
        self, status: Optional[str] = None
    ) -> list:
        return await self.repo.get_all_model_recommendations(status)

    async def get_prompt_optimizations(
        self, tenant_id: str, status: Optional[str] = None
    ) -> list:
        return await self.repo.get_prompt_optimizations(tenant_id, status)

    async def update_status(
        self,
        recommendation_id: uuid.UUID,
        rec_type: str,
        status: str,
    ) -> bool:
        valid_statuses = {"proposed", "validated", "deployed", "rejected"}
        if status not in valid_statuses:
            return False
        result = await self.repo.update_recommendation_status(
            recommendation_id, rec_type, status
        )
        if result:
            await self.session.commit()
        return result

    # ================================================================
    # PUBLIC — LLM-ASSISTED PROMPT REWRITING (Spec Step 4)
    # ================================================================

    async def generate_optimized_prompt(
        self,
        tenant_id: str,
        agent_id: str,
        original_prompt_description: str,
        avg_input_tokens: int,
        use_case: str = "general",
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> dict:
        """
        Use a lightweight LLM to generate a structurally optimized
        version of a verbose agent prompt.
        """
        from core.settings import settings

        prompt = self._build_rewrite_prompt(
            agent_id=agent_id,
            description=original_prompt_description,
            avg_input_tokens=avg_input_tokens,
            use_case=use_case,
        )

        result = await self.llm.complete(
            prompt=prompt,
            provider=provider or settings.m4_default_provider,
            model=model or settings.m4_default_model,
            max_tokens=800,
            timeout=30.0,
        )

        if not result.get("text"):
            return {
                "agent_id":               agent_id,
                "optimization_suggestions": (
                    "LLM unavailable. Manual review recommended: "
                    "remove repeated static context, add JSON output format, "
                    "shorten role description to 1-2 sentences."
                ),
                "estimated_reduction_pct": 20,
                "rewrite_template":        "",
                "tokens_used":             0,
                "provider_used":           "fallback",
                "model_used":              "rule-based",
            }

        parsed = self._parse_rewrite_response(result["text"])
        parsed["agent_id"]     = agent_id
        parsed["tokens_used"]  = result["input_tokens"] + result["output_tokens"]
        parsed["provider_used"]= result.get("provider", "unknown")
        parsed["model_used"]   = result.get("model",    "unknown")

        # --- Phase 1 & 3: Save versions and encode embeddings ---
        if parsed.get("rewrite_template"):
            # Encode description
            emb_model = get_embed_model()
            embedding = None
            if emb_model:
                try:
                    embedding = emb_model.encode(original_prompt_description).tolist()
                except Exception as e:
                    logger.warning(f"Error encoding prompt description: {e}")
            
            # Versioning
            existing = await self.repo.get_prompt_versions(tenant_id, agent_id)
            if not existing:
                await self.repo.create_prompt_version(tenant_id, agent_id, original_prompt_description)
            
            await self.repo.create_prompt_version(tenant_id, agent_id, parsed["rewrite_template"])
            
            # Record embedding clustering metric
            if embedding:
                opt_data = {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "agent_id": agent_id,
                    "use_case": use_case,
                    "optimization_type": "semantic_clustering",
                    "observed_avg_input_tokens": float(avg_input_tokens),
                    "observed_avg_output_tokens": 0.0,
                    "observed_call_count": 0,
                    "recommendation_title": "Semantic Prompt Rewrite Executed",
                    "recommendation_detail": original_prompt_description,
                    "estimated_token_reduction_pct": float(parsed.get("estimated_reduction_pct", 20)),
                    "expected_token_saving_monthly": 0,
                    "expected_cost_saving_monthly_usd": Decimal("0.0"),
                    "status": "deployed",
                    "priority": 3,
                    "embedding": embedding,
                    "created_at": datetime.now(timezone.utc),
                }
                await self.repo.create_prompt_optimization(opt_data)
                
            await self.session.commit()

        return parsed

    # ================================================================
    # PRIVATE — MODEL CLASSIFIER
    # ================================================================

    def _classify_model(
        self, tenant_id: str, profile: dict, clf: Optional[DecisionTreeClassifier] = None, tree_rules: str = ""
    ) -> Optional[dict]:
        """Classify an agent using purely the dynamically trained local ML Tree if possible."""
        model        = profile["model"]
        avg_input    = profile["avg_input_tokens"]
        avg_output   = profile["avg_output_tokens"]
        error_rate   = profile["error_rate_pct"]
        monthly_calls= profile["call_count"]
        avg_cost     = profile["avg_cost_per_call"]

        action = "keep"
        if clf is not None:
            pred = clf.predict([[avg_input, avg_output, error_rate]])[0]
            if pred == 0 and model in DOWNGRADE_MAP:
                action = "downgrade"
            elif pred == 2 and model in UPGRADE_MAP:
                action = "upgrade"
        else:
            # Fallback
            if model in DOWNGRADE_MAP and avg_input < DOWNGRADE_MAX_INPUT_TOKENS and error_rate < DOWNGRADE_MAX_ERROR_RATE:
                action = "downgrade"
            elif model in UPGRADE_MAP and error_rate > UPGRADE_MIN_ERROR_RATE:
                action = "upgrade"

        if action == "downgrade":
            target_model = DOWNGRADE_MAP[model]
            saving = self._compute_saving(profile, model, target_model, monthly_calls)
            if saving["monthly_saving"] < MIN_SAVING_THRESHOLD_USD:
                return None
            confidence = self._downgrade_confidence(avg_input, avg_output, error_rate)
            return {
                "agent_id":          profile["agent_id"],
                "current_model":     model,
                "recommended_model": target_model,
                "justification_type": "decision_tree_downgrade" if clf else "low_complexity",
                "justification_text": (
                    f"Agent '{profile['agent_id']}' matched the ML cluster Decision Tree rules "
                    f"suggesting {target_model} is sufficient. \nCluster Rules:\n{tree_rules}"
                ) if clf else (
                    f"Agent '{profile['agent_id']}' uses {model} but its profile "
                    f"suggests {target_model} is sufficient."
                ),
                "avg_input_tokens":          avg_input,
                "avg_output_tokens":         avg_output,
                "avg_cost_per_call":         avg_cost,
                "error_rate_pct":            error_rate,
                "monthly_call_count":        monthly_calls,
                "current_monthly_cost_usd":  Decimal(str(round(saving["current_cost"], 4))),
                "projected_monthly_cost_usd":Decimal(str(round(saving["projected_cost"], 4))),
                "expected_monthly_saving_usd":Decimal(str(round(saving["monthly_saving"], 4))),
                "expected_saving_pct":       round(saving["saving_pct"], 1),
                "status":                    "proposed",
                "confidence":                confidence,
                "created_at":                datetime.now(timezone.utc),
            }
            
        if action == "upgrade":
            target_model = UPGRADE_MAP[model]
            saving = self._compute_saving(profile, model, target_model, monthly_calls)
            if error_rate <= UPGRADE_MIN_ERROR_RATE and saving["monthly_saving"] >= 0:
                pass
            else:
                return {
                    "agent_id":          profile["agent_id"],
                    "current_model":     model,
                    "recommended_model": target_model,
                    "justification_type": "decision_tree_upgrade" if clf else "high_error_rate",
                    "justification_text": (
                        f"Agent '{profile['agent_id']}' matched the local cluster ML Tree rules "
                        f"indicating an upgrade is necessary to suppress errors. \nCluster Rules:\n{tree_rules}"
                    ) if clf else (
                        f"Agent '{profile['agent_id']}' shows signs of model strain."
                    ),
                    "avg_input_tokens":          avg_input,
                    "avg_output_tokens":         avg_output,
                    "avg_cost_per_call":         avg_cost,
                    "error_rate_pct":            error_rate,
                    "monthly_call_count":        monthly_calls,
                    "current_monthly_cost_usd":  Decimal(str(round(saving["current_cost"], 4))),
                    "projected_monthly_cost_usd":Decimal(str(round(abs(saving["projected_cost"]), 4))),
                    "expected_monthly_saving_usd":Decimal(str(round(saving["monthly_saving"], 4))),
                    "expected_saving_pct":       round(saving["saving_pct"], 1),
                    "status":                    "proposed",
                    "confidence":                "medium",
                    "created_at":                datetime.now(timezone.utc),
                }

        return None

    # ================================================================
    # PRIVATE — ROI COMPUTATION
    # ================================================================

    def _compute_saving(
        self,
        profile: dict,
        current_model: str,
        target_model: str,
        monthly_calls: int,
    ) -> dict:
        """
        Compute monthly cost before and after model switch.

        Uses average token counts × model pricing to estimate costs.
        The ratio of prices is the key input — actual token counts
        come from the observed profile.
        """
        avg_input  = profile["avg_input_tokens"]
        avg_output = profile["avg_output_tokens"]

        current_pricing = MODEL_PRICING.get(current_model, MODEL_PRICING["gpt-4o"])
        target_pricing  = MODEL_PRICING.get(target_model,  MODEL_PRICING["gpt-4o-mini"])

        current_cost_per_call = (
            (avg_input  / 1000) * current_pricing["input"]
            + (avg_output / 1000) * current_pricing["output"]
        )
        target_cost_per_call = (
            (avg_input  / 1000) * target_pricing["input"]
            + (avg_output / 1000) * target_pricing["output"]
        )

        current_monthly   = current_cost_per_call * monthly_calls
        projected_monthly = target_cost_per_call  * monthly_calls
        monthly_saving    = current_monthly - projected_monthly
        saving_pct        = (
            monthly_saving / current_monthly * 100
            if current_monthly > 0 else 0.0
        )

        return {
            "current_cost":   current_monthly,
            "projected_cost": projected_monthly,
            "monthly_saving": monthly_saving,
            "saving_pct":     saving_pct,
        }

    def _downgrade_confidence(
        self, avg_input: float, avg_output: float, error_rate: float
    ) -> str:
        """
        Compute confidence level for a downgrade recommendation.

        High:   very clearly a simple task (low tokens, low error rate)
        Medium: probably simple but some uncertainty
        Low:    borderline — marginal evidence for downgrade
        """
        score = 0
        if avg_input  < 200:  score += 2
        elif avg_input < 300: score += 1
        if avg_output  < 300: score += 2
        elif avg_output < 400:score += 1
        if error_rate  < 1.0: score += 2
        elif error_rate < 3.0:score += 1

        if   score >= 5: return "high"
        elif score >= 3: return "medium"
        else:            return "low"

    # ================================================================
    # PRIVATE — PROMPT PATTERN ANALYSIS
    # ================================================================

    def _analyze_prompt_patterns(
        self, tenant_id: str, profile: dict
    ) -> list[dict]:
        """
        Identify structural optimization opportunities from usage statistics.

        No raw prompts are analyzed — only statistical patterns:
        - Average input token length  (proxy for prompt verbosity)
        - Average output token length (proxy for missing output format)
        - Call frequency              (proxy for batching opportunities)

        Returns a list of optimization recommendation dicts.
        One agent can have multiple optimizations.
        """
        optimizations = []
        now        = datetime.now(timezone.utc)
        avg_input  = profile["avg_input_tokens"]
        avg_output = profile["avg_output_tokens"]
        call_count = profile["call_count"]
        agent_id   = profile["agent_id"]

        monthly_calls = call_count  # already 30-day window

        # --- Check 1: Verbose input prompts ---
        if avg_input > VERBOSE_INPUT_THRESHOLD:
            reduction_pct       = 25.0
            monthly_token_saving= int(monthly_calls * avg_input * (reduction_pct / 100))
            monthly_cost_saving = Decimal(str(round(
                monthly_calls
                * (avg_input * 0.25 / 1000)
                * MODEL_PRICING.get(profile["model"], MODEL_PRICING["gpt-4o"])["input"],
                4,
            )))
            optimizations.append({
                "id":                              uuid.uuid4(),
                "tenant_id":                       tenant_id,
                "agent_id":                        agent_id,
                "use_case":                        "general",
                "optimization_type":               "verbose_system_prompt",
                "observed_avg_input_tokens":       avg_input,
                "observed_avg_output_tokens":      avg_output,
                "observed_call_count":             call_count,
                "recommendation_title":            "Reduce verbose input prompts",
                "recommendation_detail": (
                    f"Agent '{agent_id}' averages {avg_input:.0f} input tokens per call. "
                    f"Inputs above {VERBOSE_INPUT_THRESHOLD} tokens often contain redundant "
                    f"context or instructions. Review system prompts for: repeated static "
                    f"context, verbose role descriptions, and redundant instructions. "
                    f"A 25% reduction is typically achievable without quality loss."
                ),
                "estimated_token_reduction_pct":  reduction_pct,
                "expected_token_saving_monthly":  monthly_token_saving,
                "expected_cost_saving_monthly_usd": monthly_cost_saving,
                "status":   "proposed",
                "priority": 1 if avg_input > 1200 else 2,
                "created_at": now,
            })

        # --- Check 2: High output variance suggesting missing format ---
        if avg_output > MISSING_FORMAT_THRESHOLD:
            reduction_pct       = 20.0
            monthly_token_saving= int(monthly_calls * avg_output * (reduction_pct / 100))
            monthly_cost_saving = Decimal(str(round(
                monthly_calls
                * (avg_output * 0.20 / 1000)
                * MODEL_PRICING.get(profile["model"], MODEL_PRICING["gpt-4o"])["output"],
                4,
            )))
            optimizations.append({
                "id":                              uuid.uuid4(),
                "tenant_id":                       tenant_id,
                "agent_id":                        agent_id,
                "use_case":                        "general",
                "optimization_type":               "missing_output_format",
                "observed_avg_input_tokens":       avg_input,
                "observed_avg_output_tokens":      avg_output,
                "observed_call_count":             call_count,
                "recommendation_title":            "Impose structured output format",
                "recommendation_detail": (
                    f"Agent '{agent_id}' averages {avg_output:.0f} output tokens per call. "
                    f"High output counts often indicate the model is generating verbose "
                    f"free-form text when a structured format (JSON, bullets) would suffice. "
                    f"Adding 'Respond in JSON only' or a specific schema to the prompt "
                    f"typically reduces output tokens by 15-25%."
                ),
                "estimated_token_reduction_pct":  reduction_pct,
                "expected_token_saving_monthly":  monthly_token_saving,
                "expected_cost_saving_monthly_usd": monthly_cost_saving,
                "status":   "proposed",
                "priority": 2,
                "created_at": now,
            })

        return optimizations

    # ================================================================
    # PRIVATE — LLM REWRITE HELPERS
    # ================================================================

    def _build_rewrite_prompt(
        self,
        agent_id: str,
        description: str,
        avg_input_tokens: int,
        use_case: str,
    ) -> str:
        """
        Build the token-efficient prompt for M5's LLM call.

        Strictly under 400 tokens. Never includes actual prompt content —
        only structural description and statistics.
        """
        return f"""You are an AI prompt optimization expert.

AGENT PROFILE:
Agent ID: {agent_id}
Use case: {use_case}
Current avg input tokens: {avg_input_tokens}
Prompt structure description: {description}

The agent's prompts are too long. Analyze the description and respond with ONLY this JSON:

{{
  "optimization_suggestions": "2-3 specific structural changes to reduce tokens by 20-40%. Be concrete: name what to remove or restructure.",
  "estimated_reduction_pct": 25,
  "rewrite_template": "A short template skeleton showing the optimized prompt structure. Use [PLACEHOLDER] for dynamic parts. Max 100 words."
}}

Rules:
- optimization_suggestions: actionable, specific, no generic advice
- estimated_reduction_pct: integer between 10 and 50
- rewrite_template: show structure only, never invent content
- Respond ONLY with the JSON object"""

    def _parse_rewrite_response(self, raw_text: str) -> dict:
        """Parse LLM response for prompt rewrite suggestions."""
        try:
            text = raw_text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text  = "\n".join(lines[1:-1]) if len(lines) > 2 else text
            text = text.strip()

            parsed = json.loads(text)
            return {
                "optimization_suggestions": str(
                    parsed.get("optimization_suggestions", "Review prompt for verbosity.")
                )[:1000],
                "estimated_reduction_pct": max(
                    5, min(50, int(parsed.get("estimated_reduction_pct", 20)))
                ),
                "rewrite_template": str(
                    parsed.get("rewrite_template", "")
                )[:500],
            }
        except Exception:
            return {
                "optimization_suggestions": (
                    "Reduce static context, impose JSON output format, "
                    "shorten role description."
                ),
                "estimated_reduction_pct": 20,
                "rewrite_template":        "",
            }
    
