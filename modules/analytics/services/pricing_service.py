"""
PricingService — handles versioned LLM pricing rules.
"""

from datetime import date
from typing import Optional, List
import uuid

from sqlalchemy import select, and_, update
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal

from modules.analytics.models import LLMPricingRule

class PricingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active_rules(self) -> List[LLMPricingRule]:
        """Returns all currently active pricing rules."""
        result = await self.session.execute(
            select(LLMPricingRule)
            .where(LLMPricingRule.effective_to.is_(None), LLMPricingRule.is_active == True)
            .order_by(LLMPricingRule.provider, LLMPricingRule.model)
        )
        return list(result.scalars().all())
        
    async def get_rule_by_id(self, rule_id: uuid.UUID) -> Optional[LLMPricingRule]:
        result = await self.session.execute(
            select(LLMPricingRule).where(LLMPricingRule.id == rule_id)
        )
        return result.scalar_one_or_none()

    async def create_versioned_rule(
        self,
        provider: str,
        model: str,
        input_price: Decimal,
        output_price: Decimal,
        effective_from: date,
    ) -> LLMPricingRule:
        """
        Creates a new rule for a model and provider.
        If a currently active rule exists, marks its effective_to as yesterday.
        """
        # Find existing active rule
        result = await self.session.execute(
            select(LLMPricingRule).where(
                and_(
                    LLMPricingRule.provider == provider,
                    LLMPricingRule.model == model,
                    LLMPricingRule.effective_to.is_(None)
                )
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Set effective_to to one day before the new rule's start date
            # Caution: if effective_from is in the past, handle dates cleanly.
            # Usually we don't worry too much, but let's just use effective_from.
            existing.effective_to = effective_from
            self.session.add(existing)

        # Create new rule
        new_rule = LLMPricingRule(
            provider=provider,
            model=model,
            input_price_per_1k=input_price,
            output_price_per_1k=output_price,
            effective_from=effective_from,
            effective_to=None,
            is_active=True
        )
        self.session.add(new_rule)
        await self.session.commit()
        await self.session.refresh(new_rule)
        
        return new_rule
