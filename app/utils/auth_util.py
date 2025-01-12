# app/utils/auth_util.py
from fastapi import HTTPException, Header, status
from jose import jwt, JWTError
from app.core.config import SECRET_KEY, ALGORITHM

async def verify_jwt(token: str = Header(...)) -> str:
    """
    JWT 토큰을 검증하고 사용자 ID를 반환합니다.

    Args:
        token (str): HTTP 헤더에서 전달된 JWT 토큰

    Returns:
        str: 검증된 사용자 ID (이메일)

    Raises:
        HTTPException: 토큰이 유효하지 않거나 만료된 경우
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        return user_id
    except JWTError:
        raise credentials_exception