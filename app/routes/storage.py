from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.database import get_database
from app.services.storage_service import StorageService
from app.schemas.storage import StorageListResponse, StorageDetailResponse
from app.services.image_services import verify_jwt

router = APIRouter()

@router.get("/list", response_model=StorageListResponse)
async def get_storage_list(
    user_email: str = Depends(verify_jwt),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    사용자의 전체 보관함 목록을 조회합니다.
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

@router.get("/{storage_name}", response_model=StorageDetailResponse)
async def get_storage_detail(
    storage_name: str,
    user_email: str = Depends(verify_jwt),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    특정 보관함의 상세 정보를 조회합니다.
    """
    try:
        storage_service = await StorageService.create(db)
        return await storage_service.get_storage_detail(user_email, storage_name)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch storage detail: {str(e)}"
        )