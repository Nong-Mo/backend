from fastapi import APIRouter, UploadFile, Depends, Form, File, HTTPException
from app.routes.llm import get_llm_service
from app.schemas.image import ImageUploadResponse, Point, PageVertices
from app.services.image_services import ImageService
from typing import List, Optional
from app.utils.auth_util import verify_jwt
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.database import get_database
from typing import Dict
import json
import logging
from app.services.llm_service import LLMService

router = APIRouter()
# 로깅 설정
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# ImageService 인스턴스를 생성하는 의존성 함수
async def get_image_service(
        db: AsyncIOMotorClient = Depends(get_database),
        llm_service: LLMService = Depends(get_llm_service)
):
    return ImageService(mongodb_client=db, llm_service=llm_service)


@router.post("/images/upload", response_model=ImageUploadResponse)
async def upload_images(
        storage_name: str = Form(...),
        title: str = Form(...),
        files: List[UploadFile] = File(...),
        pages_vertices_data: Optional[str] = Form(None),  # 문자열로 받음
        user_id: str = Depends(verify_jwt),
        image_service: ImageService = Depends(get_image_service)
):
    """
    이미지를 업로드하고 필요한 경우 정점 정보를 기반으로 이미지를 변환한 후 OCR 처리합니다.

    Args:
        storage_name: 업로드할 보관함 이름 ("책", "영수증", "굿즈", "필름 사진", "서류", "티켓")
        title: 사용자가 지정한 파일 제목
        files: 업로드할 이미지 파일 목록
        pages_vertices_data: 이미지별 4점 좌표 리스트 또는 null (선택적)
            예시: [
                [{x: 85.5, y: 307.8}, {x: 231.6, y: 306.8}, {x: 240.1, y: 572.4}, {x: 87.6, y: 574.5}],
                null
            ]
        user_id: JWT에서 추출한 사용자 ID (이메일)
        image_service: ImageService 인스턴스 - OCR 서비스 처리 담당
    Returns:
        ImageUploadResponse:
            - file_id: 생성된 MP3 파일의 ID (primary 파일)
            - message: 처리 결과 메시지

    Notes:
        - MP3 파일이 primary 파일로 저장되며, PDF는 연관 파일로 저장됩니다.
        - 파일 상세 조회 시 두 파일 모두 접근 가능합니다.
    """
    try:
        logger.info(f"Received pages_vertices_data: {pages_vertices_data}")
        # 문자열로 받은 정점 데이터를 파싱
        vertices_data = None
        if pages_vertices_data:
            try:
                parsed_data = json.loads(pages_vertices_data)
                logger.info(f"Parsed vertices data: {parsed_data}")
                # 데이터 검증
                if not isinstance(parsed_data, list):
                    raise HTTPException(
                        status_code=400,
                        detail="Vertices data must be a list"
                    )
                
                # null이 아닌 vertices만 포함하는 리스트 생성
                vertices_data = []
                for idx, vertices in enumerate(parsed_data):
                    logger.debug(f"Processing vertices set {idx}: {vertices}")
                    if vertices is not None:  # null이 아닌 경우에만 검증
                        if not isinstance(vertices, list) or len(vertices) != 4:
                            logger.error(f"Invalid vertices format at index {idx}: {vertices}")
                            raise HTTPException(
                                status_code=400,
                                detail="Each vertices set must have exactly 4 points"
                            )
                        for point_idx, point in enumerate(vertices):
                            logger.debug(f"Checking point {point_idx} in set {idx}: {point}")
                            if not isinstance(point, dict) or not all(k in point for k in ('x', 'y')):
                                logger.error(f"Invalid point format at index {idx}, point {point_idx}: {point}")
                                raise HTTPException(
                                    status_code=400,
                                    detail="Each point must have 'x' and 'y' coordinates"
                                )
                        vertices_data.append(vertices)
                        logger.info(f"Added vertices set {idx}: {vertices}")
                    else:
                        vertices_data.append(None)
                        logger.info(f"Added None for vertices set {idx}")

            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {str(e)}, received data: {pages_vertices_data}")
                raise HTTPException(
                    status_code=400,
                    detail="Invalid JSON format for vertices data"
                )

        logger.info(f"Final vertices_data being sent to process_images: {vertices_data}")

        result = await image_service.process_images(
            storage_name=storage_name,
            title=title,
            files=files,
            user_id=user_id,
            vertices_data=vertices_data
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
        storage_name: str = Form(...),
        title: str = Form(...),
        files: List[UploadFile] = File(...),  # 파일 목록으로 변경
        pages_vertices_data: Optional[str] = Form(None),  # 문자열로 받음
        user_id: str = Depends(verify_jwt),
        image_service: ImageService = Depends(get_image_service)
):
    """
   다중 영수증 이미지 OCR 처리

   Args:
       storage_name: 보관함 이름 ("영수증")
       title: 파일 제목
       files: 영수증 이미지 파일 목록
       pages_vertices_data: 이미지별 4점 좌표 리스트 또는 null (선택적)
            예시: [
                [{x: 85.5, y: 307.8}, {x: 231.6, y: 306.8}, {x: 240.1, y: 572.4}, {x: 87.6, y: 574.5}],
                null
            ]
       user_id: 사용자 ID
       image_service: ImageService 인스턴스 - OCR 서비스 처리 담당
   Returns:
       Dict: OCR 결과 및 파일 정보
   """
    try:
        logger.info(f"Received pages_vertices_data: {pages_vertices_data}")
        # 문자열로 받은 정점 데이터를 파싱
        vertices_data = None
        if pages_vertices_data:
            try:
                parsed_data = json.loads(pages_vertices_data)
                logger.info(f"Parsed vertices data: {parsed_data}")
                # 데이터 검증
                if not isinstance(parsed_data, list):
                    raise HTTPException(
                        status_code=400,
                        detail="Vertices data must be a list"
                    )
                
                # null이 아닌 vertices만 포함하는 리스트 생성
                vertices_data = []
                for idx, vertices in enumerate(parsed_data):
                    logger.debug(f"Processing vertices set {idx}: {vertices}")
                    if vertices is not None:  # null이 아닌 경우에만 검증
                        if not isinstance(vertices, list) or len(vertices) != 4:
                            logger.error(f"Invalid vertices format at index {idx}: {vertices}")
                            raise HTTPException(
                                status_code=400,
                                detail="Each vertices set must have exactly 4 points"
                            )
                        for point_idx, point in enumerate(vertices):
                            logger.debug(f"Checking point {point_idx} in set {idx}: {point}")
                            if not isinstance(point, dict) or not all(k in point for k in ('x', 'y')):
                                logger.error(f"Invalid point format at index {idx}, point {point_idx}: {point}")
                                raise HTTPException(
                                    status_code=400,
                                    detail="Each point must have 'x' and 'y' coordinates"
                                )
                        vertices_data.append(vertices)
                        logger.info(f"Added vertices set {idx}: {vertices}")
                    else:
                        vertices_data.append(None)
                        logger.info(f"Added None for vertices set {idx}")

            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {str(e)}, received data: {pages_vertices_data}")
                raise HTTPException(
                    status_code=400,
                    detail="Invalid JSON format for vertices data"
                )

        logger.info(f"Final vertices_data being sent to process_images: {vertices_data}")

        result = await image_service.process_receipt_ocr(
            storage_name=storage_name,
            title=title,
            files=files,
            user_id=user_id,
            vertices_data=vertices_data
        )
        return result
    except Exception as e:
        raise HTTPException(
            status_code=getattr(e, 'status_code', 500),
            detail=str(e)
        )
