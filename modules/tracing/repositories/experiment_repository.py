from typing import Optional, List
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from modules.tracing.models import LLMExperiment, ExperimentStatus

class ExperimentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_active_experiment_for_prompt(self, tenant_id: str, prompt_name: str) -> Optional[LLMExperiment]:
        """Fetch a running experiment for a specific prompt template."""
        stmt = (
            select(LLMExperiment)
            .where(
                and_(
                    LLMExperiment.tenant_id == tenant_id,
                    LLMExperiment.prompt_name == prompt_name,
                    LLMExperiment.status == ExperimentStatus.RUNNING.value
                )
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def increment_experiment_counters(self, experiment_id: str, variant: str, cost: float, duration: int, success: bool):
        """Update metrics for an experiment variant."""
        try:
            experiment = await self.session.get(LLMExperiment, experiment_id)
            if not experiment:
                return

            if variant == "A":
                experiment.a_requests += 1
                if not success:
                    experiment.a_error_count += 1
                experiment.a_avg_latency_ms = ((experiment.a_avg_latency_ms * (experiment.a_requests - 1)) + duration) / experiment.a_requests
                experiment.a_avg_cost_usd = ((experiment.a_avg_cost_usd * (experiment.a_requests - 1)) + cost) / experiment.a_requests
            elif variant == "B":
                experiment.b_requests += 1
                if not success:
                    experiment.b_error_count += 1
                experiment.b_avg_latency_ms = ((experiment.b_avg_latency_ms * (experiment.b_requests - 1)) + duration) / experiment.b_requests
                experiment.b_avg_cost_usd = ((experiment.b_avg_cost_usd * (experiment.b_requests - 1)) + cost) / experiment.b_requests

            await self.session.commit()
        except Exception:
            # Best effort metric tracking
            await self.session.rollback()
