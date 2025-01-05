from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import MONGO_URL, DATABASE_NAME

async def get_database() -> AsyncIOMotorClient:
    client = AsyncIOMotorClient(MONGO_URL)
    try:
        yield client[DATABASE_NAME]
    finally:
        client.close()