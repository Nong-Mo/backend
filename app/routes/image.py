from fastapi import APIRouter, UploadFile, HTTPException
from app.schemas.image import ImageUploadRequest, ImageUploadResponse
from app.services.image_services import ImageService
from typing import List

router = APIRouter()
image_service = ImageService()

@router.post("/images/upload", response_model=ImageUploadResponse)
async def upload_images(request: ImageUploadRequest, files: List[UploadFile]):
    result = await image_service.process_images(title=request.title, files=files)
    return ImageUploadResponse(
        file_id=result.file_id,
        message="Images processed and uploaded successfully."
    )
