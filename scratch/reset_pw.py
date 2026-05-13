import asyncio
import sys
sys.path.insert(0, ".")

from core.database import AsyncSessionLocal
from modules.auth.models import LLMAuthUser
from sqlalchemy import select
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

async def reset_all():
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(LLMAuthUser))
        users = result.scalars().all()
        
        new_hash = pwd_context.hash("Admin@1234")
        
        for user in users:
            user.hashed_password = new_hash
            
        await s.commit()
        print(f"SUCCESS: Reset password for {len(users)} users to 'Admin@1234'")
        for user in users:
            print(f" - {user.username} ({user.role})")

asyncio.run(reset_all())
