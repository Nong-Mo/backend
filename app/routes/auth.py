from fastapi import APIRouter, HTTPException, Depends, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.models.user import UserCreate, UserLogin
from app.services.auth_service import AuthService
from app.core.database import get_database
from typing import Dict, Any

router = APIRouter()


@router.post(
    "/signup",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    description="새로운 사용자를 등록합니다."
)
async def signup(
        user: UserCreate,
        db: AsyncIOMotorDatabase = Depends(get_database)
) -> Dict[str, Any]:
    """
    새로운 사용자를 등록하는 엔드포인트

    Args:
        user (UserCreate): 사용자 생성을 위한 데이터
        db: AsyncIOMotorDatabase - MongoDB 데이터베이스 인스턴스

    Returns:
        Dict[str, Any]: 생성된 사용자 정보와 성공 메시지
    """
    try:
        auth_service = await AuthService.create(db)
        created_user = await auth_service.create_user(user)
        return {
            "status": "success",
            "message": "사용자가 성공적으로 생성되었습니다.",
            "data": {
                "email": created_user["email"],
                "nickname": created_user["nickname"]
            }
        }
    except HTTPException as e:
        raise e  # create_user에서 발생한 HTTPException을 그대로 전달
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"사용자 생성 중 오류가 발생했습니다: {str(e)}"
        )


@router.post(
    "/signin",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    description="사용자 로그인을 처리합니다."
)
async def login(
        user: UserLogin,
        db: AsyncIOMotorDatabase = Depends(get_database)
) -> Dict[str, Any]:
    """
    사용자 로그인을 처리하는 엔드포인트

    Args:
        user (UserLogin): 로그인을 위한 사용자 데이터
        db: AsyncIOMotorDatabase - MongoDB 데이터베이스 인스턴스

    Returns:
        Dict[str, Any]: 액세스 토큰과 리프레시 토큰, 성공 메시지
    """
    try:
        auth_service = await AuthService.create(db)
        token_data = await auth_service.login_user(user)
        return {
            "status": "success",
            "message": "로그인에 성공했습니다.",
            "data": {
                "access_token": token_data["access_token"],
                "refresh_token": token_data["refresh_token"],
                "token_type": token_data["token_type"]
            }
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"로그인 처리 중 오류가 발생했습니다: {str(e)}"
        )

@router.post(
    "/refresh",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    description="리프레시 토큰을 사용하여 새로운 액세스 토큰을 발급합니다."
)
async def refresh_token(
        refresh_token: str,
        db: AsyncIOMotorDatabase = Depends(get_database)
) -> Dict[str, Any]:
    """
    리프레시 토큰을 사용하여 새로운 액세스 토큰을 발급하는 엔드포인트

    Args:
        refresh_token (str): 리프레시 토큰
        db: AsyncIOMotorDatabase - MongoDB 데이터베이스 인스턴스

    Returns:
        Dict[str, Any]: 새로운 액세스 토큰과 성공 메시지
    """
    try:
        auth_service = await AuthService.create(db)
        token_data = await auth_service.refresh_access_token(refresh_token)
        return {
            "status": "success",
            "message": "새로운 액세스 토큰이 발급되었습니다.",
            "data": {
                "access_token": token_data["access_token"]
            }
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"토큰 갱신 중 오류가 발생했습니다: {str(e)}"
        )

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """현재 인증된 사용자를 가져오는 의존성 함수"""
    try:
        auth_service = await AuthService.create(db)
        user = await auth_service.verify_token(credentials.credentials)
        return user
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"사용자 인증 중 오류가 발생했습니다: {str(e)}"
        )