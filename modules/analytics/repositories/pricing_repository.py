"""
PricingRepository — CRUD for llm_model_pricing.

Provides database-backed model pricing lookups, replacing
the CSV-only approach. Supports date-ranged pricing for
accurate historical cost calculations.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from modules.billing.contract_models import LLMModelPricing

logger = logging.getLogger(__name__)


class PricingRepository:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_price(
        self, model: str, target_date: date = None
    ) -> Optional[LLMModelPricing]:
        """
        Get the pricing tier for a model on a specific date.
        Returns the most recent valid pricing if no date is given.
        """
        if target_date is None:
            target_date = date.today()

        result = await self.session.execute(
            select(LLMModelPricing)
            .where(
                and_(
                    LLMModelPricing.model == model,
                    LLMModelPricing.valid_from <= target_date,
                )
            )
            .where(
                (LLMModelPricing.valid_until == None)  # noqa: E711
                | (LLMModelPricing.valid_until >= target_date)
            )
            .order_by(LLMModelPricing.valid_from.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_all_current(self) -> list[LLMModelPricing]:
        """Get all currently active pricing tiers."""
        today = date.today()
        result = await self.session.execute(
            select(LLMModelPricing)
            .where(
                LLMModelPricing.valid_from <= today,
            )
            .where(
                (LLMModelPricing.valid_until == None)  # noqa: E711
                | (LLMModelPricing.valid_until >= today)
            )
            .order_by(LLMModelPricing.model)
        )
        return list(result.scalars().all())

    async def compute_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        target_date: date = None,
    ) -> Decimal:
        """
        Compute cost for a call using database pricing.
        Falls back to zero if no pricing is found.
        """
        pricing = await self.get_price(model, target_date)
        if pricing is None:
            logger.debug(f"pricing.not_found model={model} date={target_date}")
            return Decimal("0")

        input_cost = (Decimal(input_tokens) / 1000) * pricing.input_price_per_1k
        output_cost = (Decimal(output_tokens) / 1000) * pricing.output_price_per_1k
        return round(input_cost + output_cost, 8)

    async def upsert_from_csv(self, rows: list[dict]) -> int:
        """
        Import pricing rows from CSV data.
        Each row should have: model, input_price_per_1k, output_price_per_1k, valid_from
        """
        count = 0
        for row in rows:
            model = row.get("model", "").strip()
            if not model:
                continue

            valid_from = row.get("valid_from", date.today())
            if isinstance(valid_from, str):
                valid_from = date.fromisoformat(valid_from)

            # Check if this exact pricing already exists
            existing = await self.session.execute(
                select(LLMModelPricing).where(
                    LLMModelPricing.model == model,
                    LLMModelPricing.valid_from == valid_from,
                )
            )
            if existing.scalar_one_or_none():
                continue  # skip duplicates

            provider = row.get("provider") or LLMModelPricing.infer_provider(model)

            pricing = LLMModelPricing(
                model=model,
                provider=provider,
                input_price_per_1k=Decimal(str(row.get("input_price_per_1k", 0))),
                output_price_per_1k=Decimal(str(row.get("output_price_per_1k", 0))),
                valid_from=valid_from,
            )
            self.session.add(pricing)
            count += 1

        if count:
            await self.session.flush()
            logger.info(f"pricing.imported count={count}")
        return count
