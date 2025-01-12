# app/utils/auth_util.py
from fastapi import HTTPException, Header, status, Depends
from jose import jwt, JWTError, ExpiredSignatureError
from datetime import datetime, timezone
from app.core.config import SECRET_KEY, ALGORITHM
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.database import get_database

async def verify_jwt(
    token: str = Header(...),
    db: AsyncIOMotorDatabase = Depends(get_database)
) -> str:
    """
    JWT 토큰을 검증하고 사용자 ID를 반환합니다.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        # 1. 토큰 디코딩 및 기본 검증
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # 2. 필수 클레임 확인
        user_id: str = payload.get("sub")
        if not user_id:
            raise credentials_exception
            
        # 3. 토큰 발급 시간(iat) 확인
        iat = payload.get("iat")
        if not iat:
            raise credentials_exception
            
        # 4. 사용자 존재 여부 및 상태 확인
        user = await db["users"].find_one({"email": user_id})
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
        return user_id
        
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise credentials_exception