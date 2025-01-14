import os
import uuid
import shutil
import datetime
import asyncio
from typing import List, Optional, Dict
from wsgiref.headers import Headers

from fastapi import UploadFile, HTTPException
from bson import ObjectId
import cv2
import numpy as np
from io import BytesIO
import logging

from app.routes.llm import save_story
from app.utils.ocr_util import process_ocr, process_receipt_ocr
from app.utils.tts_util import TTSUtil
from app.utils.pdf_util import PDFUtil
from app.models.image import ImageMetadata, ImageDocument
from app.core.config import S3_BUCKET_NAME

logger = logging.getLogger(__name__)


class ImageService:
    ALLOWED_STORAGE_NAMES = ["책", "영수증", "굿즈", "필름 사진", "서류", "티켓"]

    def __init__(self, mongodb_client, llm_service):
        self.db = mongodb_client
        self.storage_collection = self.db.storages
        self.files_collection = self.db.files
        self.llm_service = llm_service
        self.tts_util = TTSUtil()
        self.pdf_util = PDFUtil(mongodb_client)

    async def update_storage_count(self, user_id: ObjectId, storage_name: str, file_count: int) -> str:
        """보관함의 파일 수를 업데이트합니다."""
        storage = await self.storage_collection.find_one({
            "user_id": user_id,
            "name": storage_name
        })

        if not storage:
            raise HTTPException(status_code=404, detail=f"Storage '{storage_name}' not found")

        now = datetime.datetime.now(datetime.UTC)
        await self.storage_collection.update_one(
            {"_id": storage["_id"]},
            {
                "$inc": {"file_count": file_count},
                "$set": {"updated_at": now}
            }
        )
        return str(storage["_id"])

    async def save_file_metadata(self, storage_id: str, user_id: ObjectId, file_info: dict) -> str:
        """파일 메타데이터를 저장합니다."""
        now = datetime.datetime.now(datetime.UTC)
        file_doc = {
            "storage_id": ObjectId(storage_id),
            "user_id": user_id,
            "title": file_info["title"],
            "filename": file_info["filename"],
            "s3_key": file_info["s3_key"],
            "contents": file_info["contents"],
            "file_size": file_info["file_size"],
            "mime_type": file_info["mime_type"],
            "created_at": now,
            "updated_at": now,
            "is_primary": file_info.get("is_primary", False),
            "primary_file_id": file_info.get("primary_file_id", None)
        }

        result = await self.files_collection.insert_one(file_doc)
        return str(result.inserted_id)

    async def transform_image(self, image_bytes: bytes, vertices: List[Dict[str, float]]) -> bytes:
        """이미지를 변환합니다."""
        if len(vertices) != 4:
            raise HTTPException(status_code=400, detail="Image transformation requires exactly 4 vertices")

        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            raise HTTPException(status_code=400, detail="Invalid image data")

        src_points = np.float32([[v["x"], v["y"]] for v in vertices])

        width = max(
            np.linalg.norm(src_points[1] - src_points[0]),
            np.linalg.norm(src_points[2] - src_points[3])
        )
        height = max(
            np.linalg.norm(src_points[3] - src_points[0]),
            np.linalg.norm(src_points[2] - src_points[1])
        )

        dst_points = np.float32([
            [0, 0],
            [width - 1, 0],
            [width - 1, height - 1],
            [0, height - 1]
        ])

        matrix = cv2.getPerspectiveTransform(src_points, dst_points)
        transformed = cv2.warpPerspective(img, matrix, (int(width), int(height)))

        success, transformed_bytes = cv2.imencode('.jpg', transformed)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to encode transformed image")

        return transformed_bytes.tobytes()

    async def process_images(
            self,
            storage_name: str,
            title: str,
            files: List[UploadFile],
            user_id: str,
            vertices_data: Optional[List[Optional[List[Dict[str, float]]]]] = None
    ) -> ImageDocument:
        """이미지들을 처리하고 변환합니다."""
        if storage_name not in self.ALLOWED_STORAGE_NAMES:
            raise HTTPException(status_code=400, detail=f"Invalid storage name")

        user = await self.db["users"].find_one({"email": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        file_id = str(uuid.uuid4())
        upload_dir = f"/tmp/{user_id}/{file_id}"
        os.makedirs(upload_dir, exist_ok=True)

        storage_id = None
        try:
            storage_id = await self.update_storage_count(
                user_id=user["_id"],
                storage_name=storage_name,
                file_count=1
            )

            total_size = 0
            image_paths = []

            async def process_file(file: UploadFile, idx: int):
                nonlocal total_size

                content = await file.read()
                if not content:
                    raise HTTPException(status_code=400, detail=f"Empty file: {file.filename}")
                
                # 이미지 변환 처리
                transformed_content = content
                if vertices_data and len(vertices_data) > idx and vertices_data[idx]:
                    transformed_content = await self.transform_image(content, vertices_data[idx])

                file_path = os.path.join(upload_dir, file.filename)
                with open(file_path, "wb") as f:
                    f.write(transformed_content)
                
                image_paths.append((idx, file_path))
                total_size += len(transformed_content)

                transformed_file = UploadFile(
                    filename=file.filename,
                    file=BytesIO(transformed_content),
                    headers={"content-type": "image/jped"}
                )

                # OCR 처리 진행 
                ocr_text = await process_ocr(transformed_content)
                await transformed_file.close()

                # OCR 결과가 없는 경우 빈 결과 반환
                if not ocr_text:
                    return idx, "", None
                
                text_to_process = " ".join(ocr_text).strip()

                # TTS 요청
                if text_to_process:
                    audio_binary = await self.tts_util._get_audio_from_api(text_to_process)
                    return idx, text_to_process, audio_binary
                return idx, "", None
            
            # 파일별 작업 비동기 생성
            tasks = [process_file(file, idx) for idx, file in enumerate(files)]
            results = await asyncio.gather(*tasks)

            # 결과 정렬 (입력 순서를 보장하기 위해)
            results.sort(key=lambda x: x[0])

            # 텍스트와 오디오 바이너리 분리
            combined_text = [result[1] for result in results if result[1]]
            audio_binaries = [result[2] for result in results if result[2]]

            # 오디오 파일 병합
            final_audio = await self.tts_util._combine_mp3_files(audio_binaries)

            # S3에 업로드
            s3_key = f"tts/{file_id}/{title}.mp3"
            await asyncio.get_event_loop().run_inexecutor(
                None,
                lambda: self.tts_util.s3_client.put_object(
                    Bucket=S3_BUCKET_NAME,
                    Key=s3_key,
                    Body=final_audio,
                    ContentType='audio/mp3'
                )
            )

            file_info = {
                "title": title,
                "filename": f"combined_{file_id}",
                "s3_key": s3_key,
                "contents": combined_text,
                "file_size": total_size,
                "mime_type": "audio/mp3",
                "is_primary": True
            }

            mp3_file_id = await self.save_file_metadata(
                storage_id=storage_id,
                user_id=user["_id"],
                file_info=file_info
            )

            # PDF 생성 및 저장
            pdf_result = None
            if image_paths:
                pdf_result = await self.pdf_util.create_pdf_from_images(
                    user_id=user["_id"],
                    storage_id=storage_id,
                    image_paths=[path for _, path in image_paths],
                    pdf_title=title,
                    primary_file_id=mp3_file_id
                )

            return ImageDocument(
                title=title,
                file_id=str(mp3_file_id),
                processed_files=[
                    ImageMetadata(
                    filename=f"combined_{file_id}",
                    content_type="audio/mp3",
                    size=total_size
                    )
                ],
                created_at=datetime.datetime.now(datetime.UTC).isoformat()
            )

        except Exception as e:
            if storage_id:
                await self.storage_collection.update_one(
                    {"_id": ObjectId(storage_id)},
                    {"$inc": {"file_count": -1}}
                )
            raise HTTPException(status_code=500, detail=f"처리 중 오류 발생: {str(e)}")
        finally:
            shutil.rmtree(upload_dir, ignore_errors=True)

    async def process_receipt_ocr(
            self,
            storage_name: str,
            title: str,
            files: List[UploadFile],
            user_id: str,
            vertices_data: Optional[List[Optional[List[Dict[str, float]]]]] = None
    ) -> Dict:
        """
        영수증 이미지를 처리합니다.
        Args:
            storage_name: 보관함 이름 ("영수증")
            title: 파일 제목
            files: 업로드할 영수증 이미지 파일 목록
            user_id: 사용자 ID
            vertices_data: 이미지별 4점 좌표 리스트 또는 null (선택적)
        Returns:
            Dict: OCR 결과 및 파일 정보
        """
        storage_id = None
        group_id = str(uuid.uuid4())
        upload_dir = None

        try:
            user = await self.db["users"].find_one({"email": user_id})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            storage_id = await self.update_storage_count(
                user_id=user["_id"],
                storage_name=storage_name,
                file_count=1
            )

            file_id = str(uuid.uuid4())
            upload_dir = f"/tmp/{user_id}/{file_id}"
            os.makedirs(upload_dir, exist_ok=True)

            combined_contents = []
            image_paths = []

            # 파일 크기 계산을 위해 임시로 각 파일의 크기를 저장
            total_size = 0
            for file in files:
                file.file.seek(0)  # 파일 포인터를 처음으로 이동
                content = await file.read()
                total_size += len(content)
                file.file.seek(0)  # 파일 포인터를 다시 처음으로 이동

            for idx, file in enumerate(files):
                content = await file.read()
                if not content:
                    raise HTTPException(status_code=400, detail=f"Empty file: {file.filename}")

                transformed_content = content
                if vertices_data and len(vertices_data) > idx and vertices_data[idx]:
                    transformed_content = await self.transform_image(content, vertices_data[idx])

                file_path = os.path.join(upload_dir, file.filename)
                with open(file_path, "wb") as f:
                    f.write(transformed_content)

                image_paths.append(file_path)

                transformed_file = UploadFile(
                    filename=file.filename,
                    file=BytesIO(transformed_content),
                    headers={"content-type": "image/jpeg"}
                )

                ocr_result = await process_receipt_ocr(transformed_file)
                combined_contents.append(ocr_result)
                await transformed_file.close()

            # PDF 생성
            pdf_result = await self.pdf_util.create_pdf_from_images(
                user_id=user["_id"],
                storage_id=storage_id,
                image_paths=image_paths,
                pdf_title=title,
                storage_type="receipts"
            )

            file_info = {
                "title": title,
                "filename": f"combined_{group_id}",
                "s3_key": pdf_result["s3_key"],
                "contents": combined_contents,
                "file_size": total_size,
                "mime_type": "application/json",
                "is_primary": False,
                "primary_file_id": pdf_result["file_id"]
            }

            file_id = await self.save_file_metadata(
                storage_id=storage_id,
                user_id=user["_id"],
                file_info=file_info
            )

            return {
                "file_id": file_id,
                "ocr_results": combined_contents
            }

        except Exception as e:
            if storage_id:
                await self.storage_collection.update_one(
                    {"_id": ObjectId(storage_id)},
                    {"$inc": {"file_count": -1}}
                )
            raise HTTPException(
                status_code=500,
                detail=f"영수증 처리 중 오류 발생: {str(e)}"
            )
        finally:
            if upload_dir and os.path.exists(upload_dir):
                shutil.rmtree(upload_dir, ignore_errors=True)