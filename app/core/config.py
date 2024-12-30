import os
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# .env 파일에서 환경 변수 로드
load_dotenv()

MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    raise ValueError("MONGO_URL 환경 변수가 설정되지 않았습니다.")
DATABASE_NAME = os.getenv("DATABASE_NAME")

# 비동기 MongoDB 클라이언트 생성
client = AsyncIOMotorClient(MONGO_URL)
db = client[DATABASE_NAME]

# JWT 관련 설정 값
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES"))