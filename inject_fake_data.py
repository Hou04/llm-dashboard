import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import uuid

# Add project root to path for direct DB imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from core.database import AsyncSessionLocal
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from modules.detection.models import LLMTokenBaseline, LLMAnomalyRecord
from modules.forecasting.models import LLMForecast, LLMBudgetRisk

async def inject_anomalies():
    print("\n== Injecting Anomalies ==")
    async with AsyncSessionLocal() as session:
        # Check if baseline exists, if so delete it so we can recreate
        await session.execute(text("DELETE FROM llm_token_baseline WHERE tenant_id IN ('caveo_automotive', 'seamtech_lakatech')"))
        await session.execute(text("DELETE FROM llm_anomaly WHERE tenant_id IN ('caveo_automotive', 'seamtech_lakatech')"))
        
        # 1. Create a baseline for CAVEO
        baseline = LLMTokenBaseline(
            tenant_id="caveo_automotive",
            agent_id="*",
            model="*",
            daily_mean=150000.0,
            daily_std_dev=20000.0,
            daily_min=100000.0,
            daily_max=200000.0,
            computed_at=datetime.now(timezone.utc),
        )
        session.add(baseline)
        
        # 2. Create a 'CRITICAL' token spike anomaly for CAVEO
        anomaly1 = LLMAnomalyRecord(
            tenant_id="caveo_automotive",
            anomaly_type="token_spike",
            severity="critical",
            detector_votes="stl,isolation_forest,cusum",
            vote_count=3,
            observed_value=450000.0,
            baseline_mean=150000.0,
            baseline_std_dev=20000.0,
            description="CRITICAL: L'utilisation quotidienne (450 000 tokens) est 200% supérieure à la moyenne (150 000). Détecté par : [stl, isolation_forest, cusum].",
            detected_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        session.add(anomaly1)

        # 3. Create a 'WARNING' cost spike for SEAMTECH
        anomaly2 = LLMAnomalyRecord(
            tenant_id="seamtech_lakatech",
            anomaly_type="cost_spike",
            severity="warning",
            detector_votes="isolation_forest",
            vote_count=1,
            observed_value=12.5,
            baseline_mean=4.2,
            baseline_std_dev=1.5,
            description="WARNING: Le coût (12.50$) est significativement supérieur à la moyenne journalière (4.20$). Utilisation anormale du modèle détectée.",
            detected_at=datetime.now(timezone.utc) - timedelta(hours=5),
        )
        session.add(anomaly2)

        try:
            await session.commit()
            print("  [OK] Anomalies successfully injected.")
        except Exception as e:
            await session.rollback()
            print(f"  [ERROR] Could not inject anomalies: {e}")

async def inject_forecasts():
    print("\n== Injecting Previsions (Forecasting) ==")
    async with AsyncSessionLocal() as session:
        # Clear existing forecasts for clean injection
        await session.execute(text("DELETE FROM llm_forecast WHERE tenant_id = 'caveo_automotive'"))
        await session.execute(text("DELETE FROM llm_budget_risk WHERE tenant_id = 'caveo_automotive'"))
        
        today = datetime.now(timezone.utc).date()
        base_tokens = 150000
        base_cost = 0.50 # roughly 50 cents a day
        
        for day in range(1, 31):
            forecast_date = today + timedelta(days=day)
            
            # Pessimistic: high growth
            session.add(LLMForecast(
                tenant_id="caveo_automotive",
                agent_id="*",
                forecast_date=forecast_date,
                scenario="pessimistic",
                predicted_tokens=base_tokens + (day * 15000),
                predicted_cost_usd=Decimal(str(base_cost + (day * 0.15))),
                horizon_days=day
            ))
            
            # Likely: medium growth
            session.add(LLMForecast(
                tenant_id="caveo_automotive",
                agent_id="*",
                forecast_date=forecast_date,
                scenario="likely",
                predicted_tokens=base_tokens + (day * 5000),
                predicted_cost_usd=Decimal(str(base_cost + (day * 0.05))),
                horizon_days=day
            ))
            
            # Optimistic: low growth/flat
            session.add(LLMForecast(
                tenant_id="caveo_automotive",
                agent_id="*",
                forecast_date=forecast_date,
                scenario="optimistic",
                predicted_tokens=base_tokens + (day * 1000),
                predicted_cost_usd=Decimal(str(base_cost + (day * 0.01))),
                horizon_days=day
            ))
            
        # Add a Budget Risk alert
        risk_date = today + timedelta(days=25)
        risk = LLMBudgetRisk(
            tenant_id="caveo_automotive",
            risk_type="cost_budget",
            days_until_exhaustion=25,
            exhaustion_date=risk_date,
            forecasted_value_at_exhaustion=520.0, # exceeds the 500$ budget
            governance_limit=500.0,
            pct_of_limit_today=75.0, # currently at 75%
        )
        session.add(risk)

        try:
            await session.commit()
            print("  [OK] Forecasts successfully injected.")
        except Exception as e:
            await session.rollback()
            print(f"  [ERROR] Could not inject forecasts: {e}")

async def main():
    await inject_anomalies()
    await inject_forecasts()

if __name__ == "__main__":
    asyncio.run(main())
