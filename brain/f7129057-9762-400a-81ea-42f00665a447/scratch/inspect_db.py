import asyncio
import os
import sys

# Add current directory to path
sys.path.append(os.getcwd())

from core.database import async_session_factory
from sqlalchemy import select, delete
from modules.auth.models import LLMAuthUser
from modules.tenants.models import LLMTenant

async def inspect_and_clean():
    async with async_session_factory() as session:
        # 1. Inspect Users
        result = await session.execute(select(LLMAuthUser))
        users = result.scalars().all()
        print(f"Current Users in DB: {[u.username for u in users]}")
        
        # 2. Inspect Tenants
        result = await session.execute(select(LLMTenant))
        tenants = result.scalars().all()
        print(f"Current Tenants in DB: {[t.tenant_id for t in tenants]}")
        
        # 3. Clean Legacy Users (Keep 'admin')
        # We only keep 'admin' and any user associated with a real tenant we want
        # For now, let's just show what to delete.
        
asyncio.run(inspect_and_clean())
