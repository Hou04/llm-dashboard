"""
ContractRepository — CRUD for llm_tenant_contracts.

Used by BillingService to load contract terms dynamically
instead of looking up a hardcoded Python dict.
"""

import logging
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from modules.billing.contract_models import LLMTenantContract
from core.config_registry import config

logger = logging.getLogger(__name__)


# Default contract applied to tenants without an explicit contract
_DEFAULT_CONTRACT = {
    "contract_type": "pay_as_you_go",
    "base_fee_usd": Decimal("0"),
    "forfait_tokens": 0,
    "overage_rate_per_1k": Decimal("0"),
    "currency": "USD",
}


class ContractRepository:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_contract(self, tenant_id: str) -> Optional[LLMTenantContract]:
        """Get the active contract for a tenant. Returns None if no contract exists."""
        result = await self.session.execute(
            select(LLMTenantContract).where(
                LLMTenantContract.tenant_id == tenant_id,
                LLMTenantContract.is_active == True,
            )
        )
        return result.scalar_one_or_none()

    async def get_contract_dict(self, tenant_id: str) -> dict:
        """Get contract as a dict, falling back to pay-as-you-go if none exists."""
        contract = await self.get_contract(tenant_id)
        if contract is not None:
            return contract.to_dict()
        return dict(_DEFAULT_CONTRACT)

    async def get_or_create_default(self, tenant_id: str) -> LLMTenantContract:
        """Get existing contract or create a default pay_as_you_go contract."""
        existing = await self.get_contract(tenant_id)
        if existing is not None:
            return existing

        contract = LLMTenantContract(
            tenant_id=tenant_id,
            **_DEFAULT_CONTRACT,
        )
        self.session.add(contract)
        await self.session.flush()
        logger.info(f"contract.auto_created tenant={tenant_id} type=pay_as_you_go")
        return contract

    async def list_all_active(self) -> list[LLMTenantContract]:
        """List all active contracts."""
        result = await self.session.execute(
            select(LLMTenantContract)
            .where(LLMTenantContract.is_active == True)
            .order_by(LLMTenantContract.tenant_id)
        )
        return list(result.scalars().all())

    async def upsert(
        self,
        tenant_id: str,
        contract_type: str = "pay_as_you_go",
        base_fee_usd: float = 0.0,
        forfait_tokens: int = 0,
        overage_rate_per_1k: float = 0.0,
        currency: str = "USD",
        description: str = None,
    ) -> LLMTenantContract:
        """Create or update a contract for a tenant."""
        existing = await self.get_contract(tenant_id)
        if existing:
            existing.contract_type = contract_type
            existing.base_fee_usd = Decimal(str(base_fee_usd))
            existing.forfait_tokens = forfait_tokens
            existing.overage_rate_per_1k = Decimal(str(overage_rate_per_1k))
            existing.currency = currency
            if description:
                existing.description = description
            await self.session.flush()
            return existing

        contract = LLMTenantContract(
            tenant_id=tenant_id,
            contract_type=contract_type,
            base_fee_usd=Decimal(str(base_fee_usd)),
            forfait_tokens=forfait_tokens,
            overage_rate_per_1k=Decimal(str(overage_rate_per_1k)),
            currency=currency,
            description=description,
        )
        self.session.add(contract)
        await self.session.flush()
        logger.info(
            f"contract.upserted tenant={tenant_id} type={contract_type} "
            f"base_fee={base_fee_usd} forfait={forfait_tokens}"
        )
        return contract

    async def auto_assign_from_usage(
        self, tenant_id: str, monthly_tokens: int
    ) -> LLMTenantContract:
        """
        Auto-assign a contract tier based on observed usage volume.

        Tier boundaries and pricing are loaded from ConfigRegistry,
        making them adjustable at runtime without code changes.
        """
        existing = await self.get_contract(tenant_id)
        if existing:
            return existing  # don't override existing contracts

        # Tier boundaries — configurable via ConfigRegistry
        medium_threshold = config.get("billing.tier.medium_tokens", 1_000_000)
        enterprise_threshold = config.get("billing.tier.enterprise_tokens", 10_000_000)

        if monthly_tokens < medium_threshold:
            return await self.upsert(
                tenant_id=tenant_id,
                contract_type="pay_as_you_go",
                description="Auto-assigned: low usage tier",
            )
        elif monthly_tokens < enterprise_threshold:
            return await self.upsert(
                tenant_id=tenant_id,
                contract_type="forfait",
                base_fee_usd=config.get("billing.tier.medium_base_fee", 50.00),
                forfait_tokens=config.get("billing.tier.medium_forfait_tokens", 5_000_000),
                overage_rate_per_1k=config.get("billing.tier.medium_overage_rate", 0.002),
                description="Auto-assigned: medium usage tier",
            )
        else:
            return await self.upsert(
                tenant_id=tenant_id,
                contract_type="forfait",
                base_fee_usd=config.get("billing.tier.enterprise_base_fee", 150.00),
                forfait_tokens=config.get("billing.tier.enterprise_forfait_tokens", 50_000_000),
                overage_rate_per_1k=config.get("billing.tier.enterprise_overage_rate", 0.003),
                description="Auto-assigned: enterprise usage tier",
            )
