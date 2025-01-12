from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.database import get_database
from app.services.storage_service import StorageService
from app.schemas.storage import StorageListResponse, StorageDetailResponse, PDFConversionRequest, PDFConversionResponse, FileDetailResponse
from app.utils.auth_util import verify_jwt
from typing import List

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
        
        # 한글로 변경
        korean_storage_name = storage_name_mapping.get(storage_name, storage_name)
        
        # DB에서 상세 정보 조회
        storage_service = await StorageService.create(db)
        return await storage_service.get_storage_detail(user_email, korean_storage_name)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch storage detail: {str(e)}"
        )

@router.get("/files/{file_id}", response_model=FileDetailResponse)
async def get_file_detail(
    file_id: str,
    user_email: str = Depends(verify_jwt),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    특정 파일의 상세 정보와 URL을 조회합니다.
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
        
@router.post("/files/{file_id}/recent")
async def update_recent_date(
    file_id: str,
    user_email: str = Depends(verify_jwt),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    특정 파일의 최근 열람일을 갱신합니다.
    """
    try:
        storage_service = await StorageService.create(db)
        await storage_service.update_recent_date(user_email, file_id)
        return {"message": "Recent date updated successfully"}
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update recent date: {str(e)}"
        )

@router.delete("/files/{file_id}")
async def delete_file(
    file_id: str,
    user_email: str = Depends(verify_jwt),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    특정 파일을 삭제합니다.
    """
    try:
        storage_service = await StorageService.create(db)
        await storage_service.delete_file(user_email, file_id)
        return {"message": "File deleted successfully"}
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete file: {str(e)}"
        )

@router.post("/convert-to-pdf", response_model=PDFConversionResponse)
async def convert_to_pdf(
    request: PDFConversionRequest,
    user_email: str = Depends(verify_jwt),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    선택된 이미지들을 PDF로 변환합니다.
    """
    try:
        storage_service = await StorageService.create(db)
        return await storage_service.convert_to_pdf(
            user_email=user_email,
            file_ids=request.file_ids,
            pdf_title=request.pdf_title
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to convert images to PDF: {str(e)}"
        )