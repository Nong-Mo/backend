from fastapi import APIRouter, UploadFile, Depends, Form, File, HTTPException
from app.schemas.image import ImageUploadResponse
from app.services.image_services import ImageService
from typing import List
from app.services.image_services import verify_jwt
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.database import get_database
#import pdb

router = APIRouter()

# ImageService 인스턴스를 생성하는 의존성 함수
async def get_image_service(db: AsyncIOMotorClient = Depends(get_database)):
    return ImageService(mongodb_client=db)

@router.post("/images/upload", response_model=ImageUploadResponse)
async def upload_images(
    title: str = Form(...),
    files: List[UploadFile] = File(...),
    user_id: str = Depends(verify_jwt),
    image_service: ImageService = Depends(get_image_service)
):
    """
    이미지를 업로드하고 OCR을 수행하여 결과를 반환
    """
    try:
        result = await image_service.process_images(
            title=title,
            files=files,
            user_id=user_id
        )
        return ImageUploadResponse(
            file_id=result.file_id,
            message="Images processed and uploaded successfully."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
