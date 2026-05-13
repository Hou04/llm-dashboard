
import asyncio
import sys
import os

# Add the project root to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from core.database import async_session_factory

async def check():
    print("--- DATABASE DIAGNOSTIC ---")
    try:
        async with async_session_factory() as session:
            # 1. Check total logs
            res = await session.execute(text("SELECT COUNT(*) FROM llm_token_log"))
            count = res.scalar()
            print(f"Total Logs in DB: {count}")
            
            # 2. Check tenant IDs
            res = await session.execute(text("SELECT DISTINCT tenant_id FROM llm_token_log"))
            tenants = [row[0] for row in res.all()]
            print(f"Tenants with data: {tenants}")
            
            # 3. Check latest logs
            if count > 0:
                res = await session.execute(text("SELECT tenant_id, model, cost_usd, created_at FROM llm_token_log ORDER BY created_at DESC LIMIT 5"))
                print("\nLatest 5 Logs:")
                for row in res.all():
                    print(f" - {row.created_at} | Tenant: {row.tenant_id} | Model: {row.model} | Cost: ${row.cost_usd}")
            else:
                print("\n⚠️  WARNING: No logs found. This means your Celery Worker is NOT saving the data.")
    except Exception as e:
        print(f"Error connecting to DB: {e}")

if __name__ == "__main__":
    asyncio.run(check())
