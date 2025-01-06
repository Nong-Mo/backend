from fastapi import APIRouter, UploadFile, Depends, Form, File, HTTPException
from app.schemas.image import ImageUploadResponse
from app.services.image_services import ImageService
from typing import List
from app.services.image_services import verify_jwt
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.database import get_database

router = APIRouter()

# ImageService 인스턴스를 생성하는 의존성 함수
async def get_image_service(db: AsyncIOMotorClient = Depends(get_database)):
    return ImageService(mongodb_client=db)

@router.post("/images/upload", response_model=ImageUploadResponse)
async def upload_images(
    storage_name: str = Form(...),  # 보관함 이름 ("책", "영수증" 등)
    title: str = Form(...),         # 사용자가 지정한 파일 제목
    files: List[UploadFile] = File(...),
    user_id: str = Depends(verify_jwt),
    image_service: ImageService = Depends(get_image_service)
):
    """
    이미지를 업로드하고 OCR을 수행하여 결과를 반환

    Args:
        storage_name: 업로드할 보관함 이름 ("책", "영수증", "굿즈", "필름 사진", "서류", "티켓")
        title: 사용자가 지정한 파일 제목
        files: 업로드할 이미지 파일 목록
        user_id: JWT에서 추출한 사용자 ID (이메일)
    """
    try:
        result = await image_service.process_images(
            storage_name=storage_name,
            title=title,
            files=files,
            user_id=user_id
        )
        return ImageUploadResponse(
            file_id=result.file_id,
            message="Images processed and uploaded successfully."
        )
    except Exception as e:
        # 에러 메시지를 그대로 클라이언트에 전달
        raise HTTPException(
            status_code=getattr(e, 'status_code', 500),
            detail=str(e)
        )