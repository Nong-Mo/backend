from passlib.context import CryptContext
from app.models.user import UserCreate, UserLogin
from app.core.config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES
from fastapi import HTTPException, status
from datetime import datetime, timezone, timedelta
from jose import jwt
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId


class AuthService:
    """인증 관련 서비스를 제공하는 클래스"""

    # 비밀번호 암호화에 사용될 패스워드 컨텍스트
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    # 기본 보관함 목록
    DEFAULT_STORAGE_NAMES = ["책", "영수증", "굿즈", "필름 사진", "서류", "티켓"]

    def __init__(self, db: AsyncIOMotorDatabase):
        """
        AuthService 초기화

        Args:
            db: AsyncIOMotorDatabase - MongoDB 데이터베이스 인스턴스
        """
        self.db = db
        self.users_collection = db["users"]
        self.storages_collection = db["storages"]

    @classmethod
    async def create(cls, db: AsyncIOMotorDatabase):
        """
        AuthService 인스턴스 생성을 위한 팩토리 메서드

        Args:
            db: AsyncIOMotorDatabase - MongoDB 데이터베이스 인스턴스
        """
        return cls(db)

    async def hash_password(self, password: str) -> str:
        """비밀번호를 해시화"""
        return self.pwd_context.hash(password)

    async def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """비밀번호 검증"""
        return self.pwd_context.verify(plain_password, hashed_password)

    def is_valid_password(self, password: str) -> bool:
        """
        비밀번호가 유효한지 검사하는 함수입니다.
        """
        if not 8 <= len(password) <= 20 or " " in password:
            return False

        char_types = [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
        return sum(char_types) >= 2

    def create_access_token(self, data: dict, expires_delta: timedelta = None):
        """JWT 토큰 생성"""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=15)
        to_encode.update({"exp": expire})
        return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

    async def create_default_storages(self, user_id: ObjectId):
        """
        사용자의 기본 보관함을 생성합니다.

        Args:
            user_id: ObjectId - 사용자 ID
        """
        current_time = datetime.now(timezone.utc)
        storage_documents = []

        for storage_name in self.DEFAULT_STORAGE_NAMES:
            storage = {
                "user_id": user_id,
                "name": storage_name,
                "file_count": 0,
                "created_at": current_time,
                "updated_at": current_time
            }
            storage_documents.append(storage)

        if storage_documents:
            await self.storages_collection.insert_many(storage_documents)

    async def create_user(self, user: UserCreate):
        """
        새로운 사용자를 생성하고 기본 보관함을 설정합니다.

        Args:
            user: UserCreate - 생성할 사용자 정보

        Raises:
            HTTPException:
                - 400: 비밀번호가 일치하지 않거나 유효하지 않은 경우
                - 409: 이미 등록된 이메일인 경우
                - 500: 서버 오류
        """
        if user.password != user.password_confirmation:
            raise HTTPException(status_code=400, detail="Passwords do not match")

        if not self.is_valid_password(user.password):
            raise HTTPException(
                status_code=400,
                detail="Password must be 8-20 characters long, contain at least two character types (uppercase, lowercase, numbers, special characters), and no spaces."
            )

        # 닉네임 길이 제한 추가
        if len(user.nickname) > 8:
            raise HTTPException(status_code=400, detail="Nickname must be 8 characters or less")

        existing_user = await self.users_collection.find_one({"email": user.email})
        if existing_user:
            raise HTTPException(status_code=409, detail="Email already registered")

        try:
            # 1. 사용자 생성
            hashed_password = await self.hash_password(user.password)
            new_user = {
                "email": user.email,
                "nickname": user.nickname,
                "password": hashed_password,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc)
            }

            result = await self.users_collection.insert_one(new_user)
            user_id = result.inserted_id

            # 2. 기본 보관함 생성
            await self.create_default_storages(user_id)

            # 3. 생성된 사용자 정보 조회
            created_user = await self.users_collection.find_one({"_id": user_id})
            if not created_user:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="사용자 생성 후 조회 실패"
                )

            return created_user

        except Exception as e:
            # 에러 발생 시 생성된 데이터 롤백
            if 'user_id' in locals():
                await self.users_collection.delete_one({"_id": user_id})
                await self.storages_collection.delete_many({"user_id": user_id})
            raise HTTPException(status_code=500, detail=str(e))

    def create_access_token(self, data: dict, expires_delta: timedelta = None):
        """Access 토큰 생성"""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=15)
        to_encode.update({"exp": expire})
        return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

    def create_refresh_token(self, data: dict):
        """Refresh 토큰 생성"""
        to_encode = data.copy()
        expire = datetime.now(timezone.utc) + timedelta(days=7)
        to_encode.update({"exp": expire})
        return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

    async def verify_token(self, token: str):
        """JWT 토큰을 검증하는 메서드"""
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            email: str = payload.get("sub")
            if email is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid authentication credentials"
                )
            
            user = await self.users_collection.find_one({"email": email})
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User not found"
                )
                
            return user
            
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials"
            )

    async def refresh_access_token(self, refresh_token: str):
        """리프레시 토큰으로 새로운 액세스 토큰 발급"""
        try:
            payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
            email: str = payload.get("sub")
            if email is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid refresh token"
                )

            access_token = self.create_access_token({"sub": email})
            return {"access_token": access_token}

        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token"
            )

    async def login_user(self, user: UserLogin):
        """사용자 로그인을 처리합니다."""
        existing_user = await self.users_collection.find_one({"email": user.email})
        if not existing_user:
            raise HTTPException(status_code=401, detail="Invalid email or password")

        if not await self.verify_password(user.password, existing_user["password"]):
            raise HTTPException(status_code=401, detail="Invalid email or password")

        # Access Token과 Refresh Token 모두 발급
        access_token = self.create_access_token({"sub": user.email})
        refresh_token = self.create_refresh_token({"sub": user.email})
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
        }