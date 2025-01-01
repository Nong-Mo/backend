from fastapi import APIRouter, Depends
from app.services.storage_service import StorageService
from app.schemas.storage import StorageListResponse
from app.services.image_services import verify_jwt

router = APIRouter()
sotrage_service = StorageService()

@router.get("/storages", response_model=StorageListResponse)
async def get_storage_info(user_email: str = Depends(verify_jwt)):
    '''
    사용자의 스토리지 정보를 조회합니다.
    JWT 토큰이 필요합니다.
    '''
    return # stoarge_service 