from fastapi import APIRouter, UploadFile, Depends, Form, File
from app.schemas.image import ImageUploadRequest, ImageUploadResponse
from app.services.image_services import ImageService
from typing import List
from app.services.image_services import verify_jwt
import pdb

router = APIRouter()
image_service = ImageService()

@router.post("/images/upload", response_model=ImageUploadResponse)
async def upload_images(
    title: str = Form(...),  # ImageUploadRequest 대신 필요한 필드들을 Form으로 받음
    files: List[UploadFile] = File(...),
    user_id: str = Depends(verify_jwt)
):
    """
    이미지를 업로드하고 OCR을 수행하여 결과를 반환
    """
    # pdb.set_trace()
    result = await image_service.process_images(title=title, files=files, user_id=user_id)
    return ImageUploadResponse(
        file_id=result.file_id,
        message="Images processed and uploaded successfully."
    )
