"""
AnomalyService — handles integration with litellm to auto-detect pricing shifts.
"""
from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

# Uses LiteLLM's internal pricing context directly
from litellm import model_cost
from decimal import Decimal

from modules.analytics.services.pricing_service import PricingService

class AnomalyService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.pricing_service = PricingService(session)

    async def detect_pricing_anomalies(self) -> List[Dict[str, Any]]:
        """
        Loads all active DB pricing rules and cross-references them against Litellm.
        Flags any entry where DB rate substantially diverges from LiteLLM's known upstream rate.
        """
        active_rules = await self.pricing_service.list_active_rules()
        anomalies = []

        for rule in active_rules:
            # LiteLLM formats model names directly usually or provider/model
            # Wait, LiteLLM keys are typically just model names e.g. "gpt-4o"
            litellm_entry = model_cost.get(rule.model)
            
            if not litellm_entry:
                # Might need prefix format check e.g. "openai/gpt-4o"
                litellm_entry = model_cost.get(f"{rule.provider}/{rule.model}")
            
            if not litellm_entry:
                continue

            # LiteLLM cost is usually per 'token' not per '1k tokens' in some versions,
            # or it has "input_cost_per_token" and "output_cost_per_token".
            # We multiply by 1000 to normalize to DB format.
            base_in = litellm_entry.get("input_cost_per_token", 0)
            base_out = litellm_entry.get("output_cost_per_token", 0)
            
            # Normalize to 1K tokens
            litellm_input_1k = Decimal(str(base_in)) * 1000
            litellm_output_1k = Decimal(str(base_out)) * 1000

            db_in = rule.input_price_per_1k
            db_out = rule.output_price_per_1k

            # Anomaly detected if mismatch (with minor floating tolerance)
            is_anomaly = abs(litellm_input_1k - db_in) > Decimal("0.000001") or \
                         abs(litellm_output_1k - db_out) > Decimal("0.000001")
            
            if is_anomaly:
                anomalies.append({
                    "provider": rule.provider,
                    "model": rule.model,
                    "db_input_price": str(db_in),
                    "db_output_price": str(db_out),
                    "litellm_input_price": str(litellm_input_1k),
                    "litellm_output_price": str(litellm_output_1k),
                    "anomaly_detected": True
                })

        return anomalies
