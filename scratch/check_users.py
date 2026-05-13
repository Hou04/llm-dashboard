import asyncio
from core.database import AsyncSessionLocal
from modules.auth.models import LLMAuthUser
from sqlalchemy import select

async def check():
    async with AsyncSessionLocal() as s:
        r = await s.execute(select(LLMAuthUser.username, LLMAuthUser.role, LLMAuthUser.hashed_password))
        users = r.all()
        print(f"Found {len(users)} users:")
        for u in users:
            print(f" - {u.username} ({u.role}) | hash: {u.hashed_password[:20]}...")

if __name__ == "__main__":
    asyncio.run(check())
