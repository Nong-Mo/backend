# app/utils/auth_util.py
from fastapi import HTTPException, Header, status, Depends
from jose import jwt, JWTError, ExpiredSignatureError
from datetime import datetime, timezone
from app.core.config import SECRET_KEY, ALGORITHM, JWT_MIN_LENGTH, JWT_MAX_AGE
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.database import get_database

async def verify_jwt(authorization: str = Header(...)) -> str:
    """
    JWT 토큰을 검증하고 사용자 ID를 반환합니다.

    Args:
        authorization (str): HTTP Authorization 헤더 값

    Returns:
        str: 검증된 사용자 ID (이메일)

    Raises:
        HTTPException: 
            - 401: 토큰이 유효하지 않거나 만료된 경우
            - 403: 토큰의 형식이 잘못된 경우
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid authentication scheme",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.split(" ")[1]

    # 토큰 길이 검증
    if len(token) < JWT_MIN_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid token format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        # 토큰 디코딩 및 검증
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # 필수 클레임 검증
        if not all(k in payload for k in ["sub", "exp", "iat", "type"]):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid token claims",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 토큰 타입 검증
        if payload["type"] != "access_token":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid token type",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 토큰 발급 시간 검증
        iat = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
        if datetime.now(timezone.utc) - iat > JWT_MAX_AGE:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user_id: str = payload["sub"]
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )