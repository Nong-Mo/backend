from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.database import get_database
from app.services.storage_service import StorageService
from app.schemas.storage import StorageListResponse, StorageDetailResponse, AudioFileDetail
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
        # 영어로 된 보관함 이름을 한글로 변환
        storage_name_mapping = {
            "book": "책",
            "receipt": "영수증", 
            "goods": "굿즈",
            "film": "필름 사진",
            "document": "서류",
            "ticket": "티켓"
        }
        
        korean_storage_name = storage_name_mapping.get(storage_name, storage_name)
        
        storage_service = await StorageService.create(db)
        return await storage_service.get_storage_detail(user_email, korean_storage_name)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch storage detail: {str(e)}"
        )

@router.get("/file/{file_id}", response_model=AudioFileDetail)
async def get_file_detail(
    file_id: str,
    user_email: str = Depends(verify_jwt),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    특정 파일의 상세 정보와 오디오 URL을 조회합니다.
    """
    try:
        storage_service = await StorageService.create(db)
        return await storage_service.get_file_detail(user_email, file_id)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch file detail: {str(e)}"
        )