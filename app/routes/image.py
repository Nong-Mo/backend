from fastapi import APIRouter, UploadFile, Depends
from app.schemas.image import ImageUploadRequest, ImageUploadResponse
from app.services.image_services import ImageService
from typing import List
from app.services.image_services import verify_jwt

router = APIRouter()
image_service = ImageService()

@router.post("/images/upload", response_model=ImageUploadResponse)
async def upload_images(request: ImageUploadRequest, files: List[UploadFile], user_info: dict = Depends(verify_jwt)):
    """
    이미지를 업로드하고 OCR을 수행하여 결과를 반환
    """
    result = await image_service.process_images(title=request.title, files=files, user_info=user_info)
    return ImageUploadResponse(
        file_id=result.file_id,
        message="Images processed and uploaded successfully."
    )