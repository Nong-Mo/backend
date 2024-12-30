from passlib.context import CryptContext
from app.models.user import UserCreate
from app.core.config import db, SECRET_KEY, ALGORITHM
from fastapi import HTTPException
from datetime import datetime, timezone, timedelta
from jose import jwt

datetime.datetime.now(timezone.utc)

# 비밀번호 암호화에 사용될 패스워드 컨텍스트
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# MongoDB users 컬렉션
users_collection = db["users"]

async def hash_password(password: str) -> str:
    hashed_password = pwd_context.hash(password)
    return hashed_password

async def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def is_valid_password(password: str) -> bool:
    """
        비밀번호가 유효한지 검사하는 함수입니다.

        다음 조건을 모두 만족해야 합니다.
        - 8자 이상 20자 이하
        - 공백 없음
        - 대문자, 소문자, 숫자, 특수문자 중 2종류 이상 포함

        Args:
            password (str): 검사할 비밀번호

        Returns:
            bool: 비밀번호가 유효하면 True, 아니면 False
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

async def create_user(user: UserCreate):
    if user.password != user.password_confirmation:
        raise HTTPException(status_code=400, detail="Passwords do not match")

    # 비밀번호 복잡성 검증
    if not is_valid_password(user.password):
        raise HTTPException(
            status_code=400,
            detail="Password must be 8-20 characters long, contain at least two character types (uppercase, lowercase, numbers, special characters), and no spaces."
        )

    # 중복 이메일 체크
    existing_user = await users_collection.find_one({"email": user.email})
    if existing_user:
        raise HTTPException(status_code=409, detail="Email already registered")

    hashed_password = await hash_password(user.password)
    new_user = {
        "email": user.email,
        "nickname": user.nickname,
        "password": hashed_password,
    }
    try:
        await users_collection.insert_one(new_user)
    except Exception as e:  # 만약 이미 존재하는 키(중복)로 인한 예외가 발생하면 ValueError로 처리
        raise HTTPException(status_code=409, detail="Email already registered")

def create_access_token(data: dict, expires_delta: timedelta = None):
    # JWT 토큰 생성
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt