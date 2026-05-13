import asyncio
import os
import sys

# Add current directory to path
sys.path.append(os.getcwd())

from core.database import async_session_factory
from sqlalchemy import text
from modules.auth.models import LLMAuthUser

async def purge_legacy_data():
    async with async_session_factory() as session:
        print("Cleaning up database for PFE presentation...")
        
        # 1. Clear telemetry and logs (M1/M7)
        await session.execute(text("TRUNCATE TABLE llm_token_log CASCADE"))
        await session.execute(text("TRUNCATE TABLE llm_governance_decisions CASCADE"))
        
        # 2. Clear tenants and credentials
        await session.execute(text("TRUNCATE TABLE llm_tenant_credentials CASCADE"))
        await session.execute(text("TRUNCATE TABLE llm_virtual_keys CASCADE"))
        await session.execute(text("TRUNCATE TABLE llm_tenants CASCADE"))
        
        # 3. Clear users (EXCEPT 'admin')
        await session.execute(
            text("DELETE FROM llm_auth_users WHERE username != 'admin'")
        )
        
        await session.commit()
        print("Done! Database is now clean.")
        print("Only 'admin' user remains.")

if __name__ == "__main__":
    asyncio.run(purge_legacy_data())
