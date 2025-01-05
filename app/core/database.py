from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from typing import AsyncGenerator
from app.core.config import MONGO_URL, DATABASE_NAME

async def get_database() -> AsyncGenerator[AsyncIOMotorDatabase, None]:
    """
    데이터베이스 연결을 생성하고 관리하는 의존성 함수
    """
    client = AsyncIOMotorClient(MONGO_URL)
    try:
        db = client[DATABASE_NAME]
        # 연결 테스트
        await db.command('ping')
        yield db
    except Exception as e:
        print(f"데이터베이스 연결 실패: {e}")
        raise
    finally:
        client.close()