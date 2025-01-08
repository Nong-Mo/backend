from fastapi import APIRouter, UploadFile, Depends, Form, File, HTTPException
from app.schemas.image import ImageUploadResponse
from app.services.image_services import ImageService
from typing import List
from app.services.image_services import verify_jwt
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.database import get_database
from typing import Dict

router = APIRouter()

# ImageService 인스턴스를 생성하는 의존성 함수
async def get_image_service(db: AsyncIOMotorClient = Depends(get_database)):
    return ImageService(mongodb_client=db)

@router.post("/images/upload", response_model=ImageUploadResponse)
async def upload_images(
        storage_name: str = Form(...),
        title: str = Form(...),
        files: List[UploadFile] = File(...),
        user_id: str = Depends(verify_jwt),
        image_service: ImageService = Depends(get_image_service)
):
    """
    이미지를 업로드하고 OCR 처리 후 MP3와 PDF 파일을 생성합니다.

    Args:
        storage_name: 업로드할 보관함 이름 ("책", "영수증", "굿즈", "필름 사진", "서류", "티켓")
        title: 사용자가 지정한 파일 제목
        files: 업로드할 이미지 파일 목록
        user_id: JWT에서 추출한 사용자 ID (이메일)

    Returns:
        ImageUploadResponse:
            - file_id: 생성된 MP3 파일의 ID (primary 파일)
            - message: 처리 결과 메시지

    Notes:
        - MP3 파일이 primary 파일로 저장되며, PDF는 연관 파일로 저장됩니다.
        - 파일 상세 조회 시 두 파일 모두 접근 가능합니다.
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
            message="Files processed and stored successfully. Access both MP3 and PDF through file details."
        )
    except Exception as e:
        raise HTTPException(
            status_code=getattr(e, 'status_code', 500),
            detail=str(e)
        )

@router.post("/receipt/ocr", response_model=Dict)
async def process_receipt_ocr(
        storage_name: str = Form(...),  # 보관함 이름 ("영수증")
        title: str = Form(...),  # 사용자가 지정한 파일 제목
        file: UploadFile = File(...),
        user_id: str = Depends(verify_jwt),
        image_service: ImageService = Depends(get_image_service)
):
    """
    영수증 이미지를 업로드하고 특화된 OCR을 수행하여 결과를 반환 및 저장
    Args:
        storage_name: 업로드할 보관함 이름 ("영수증")
        title: 사용자가 지정한 파일 제목
        file: 업로드할 영수증 이미지 파일
        user_id: JWT에서 추출한 사용자 ID
        image_service: OCR 서비스를 처리하는 ImageService 인스턴스
    Returns:
        Dict: OCR 결과 및 저장된 파일 정보
    """
    try:
        result = await image_service.process_receipt_ocr(
            storage_name=storage_name,
            title=title,
            file=file,
            user_id=user_id
        )
        return result
    except Exception as e:
        raise HTTPException(
            status_code=getattr(e, 'status_code', 500),
            detail=str(e)
        )