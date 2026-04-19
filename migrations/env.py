import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ============================================================
# IMPORT YOUR MODELS HERE
#
# Every model that should be tracked by Alembic migrations
# must be imported here. Alembic inspects Base.metadata to
# discover all tables and compare against the database.
#
# Add new module model imports below as you build each module.
# ============================================================
from core.database import Base
from modules.gateway.models import LLMTokenLog, LLMGovernanceRule, LLMGovernanceDecision  # noqa: F401
from modules.analytics.models import LLMCostDaily, LLMCostMonthly, LLMPricingRule
from modules.detection.models import LLMTokenBaseline, LLMAnomalyRecord
from modules.forecasting.models import LLMForecast, LLMBudgetRisk
from modules.detection.models_explainer import LLMTokenExplanation, LLMTokenRecommendation
from modules.forecasting.models_optimizer import LLMPromptOptimization, LLMModelRecommendation
from modules.billing.models import LLMBillingMonthly, LLMBillingLineItem, LLMClientReport
from modules.billing.contract_models import LLMTenantContract, LLMModelPricing
from modules.billing.contract_models import LLMTenantContract, LLMModelPricing
from modules.auth.models import LLMAuthUser, LLMApiKey
from modules.tenants.models import LLMTenant
# This is the Alembic Config object
config = context.config

# Set up logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# This is the metadata object that Alembic compares against the DB
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.
    Generates SQL scripts without connecting to the database.
    Useful for reviewing what Alembic will do before applying it.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Run migrations in 'online' mode with an async engine.
    This is the mode used when you run 'alembic upgrade head'.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migrations — called by Alembic."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()