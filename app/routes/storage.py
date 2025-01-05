from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.database import get_database
from app.services.storage_service import StorageService
from app.schemas.storage import StorageListResponse
from app.services.image_services import verify_jwt

router = APIRouter()

@router.get("/storages", response_model=StorageListResponse)
async def get_storage_info(
    user_email: str = Depends(verify_jwt),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    사용자의 보관함 목록을 조회합니다.
    
    Args:
        user_email (str): JWT 토큰에서 추출한 사용자 이메일
        db: AsyncIOMotorDatabase - MongoDB 데이터베이스 인스턴스
        
    Returns:
        StorageListResponse: 사용자의 닉네임과 보관함 목록
        
    Raises:
        HTTPException: 인증 실패 또는 서버 오류 발생 시
    """
    try:
        storage_service = await StorageService.create(db)
        return await storage_service.get_storage_list(user_email)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch storage list: {str(e)}"
        ) 