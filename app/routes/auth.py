from fastapi import APIRouter, HTTPException, Depends, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.models.user import UserCreate, UserLogin
from app.services.auth_service import AuthService
from app.core.database import get_database
from app.utils.auth_util import verify_jwt
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
        Dict[str, Any]: 액세스 토큰과 성공 메시지
    """
    try:
        auth_service = await AuthService.create(db)
        token_data = await auth_service.login_user(user)
        return {
            "status": "success",
            "message": "로그인에 성공했습니다.",
            "data": {
                "access_token": token_data["access_token"],
                "token_type": token_data["token_type"]
            }
        }
    except HTTPException as e:
        raise e  # login_user에서 발생한 HTTPException을 그대로 전달
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"로그인 처리 중 오류가 발생했습니다: {str(e)}"
        )
    
@router.get(
    "/verify",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    description="JWT 토큰의 유효성을 검증합니다."
)
async def verify_token(
    user_id: str = Depends(verify_jwt),
    db: AsyncIOMotorDatabase = Depends(get_database)
) -> Dict[str, Any]:
    """
    JWT 토큰의 유효성을 검증하는 엔드포인트
    
    Args:
        user_id: JWT 토큰에서 추출한 사용자 ID (이메일)
        db: AsyncIOMotorDatabase - MongoDB 데이터베이스 인스턴스

    Returns:
        Dict[str, Any]: 검증 결과와 사용자 정보

    Raises:
        HTTPException: 
            - 401: 토큰이 유효하지 않은 경우
            - 404: 사용자를 찾을 수 없는 경우
    """
    try:
        # 사용자 존재 여부 확인
        user = await db["users"].find_one({"email": user_id})
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # 토큰이 유효하고 사용자도 존재하는 경우
        return {
            "status": "success",
            "message": "Token is valid",
            "data": {
                "email": user["email"],
                "nickname": user["nickname"]
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Token verification failed: {str(e)}"
        )