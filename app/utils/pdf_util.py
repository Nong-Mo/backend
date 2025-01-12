# app/utils/pdf_util.py
import os
import uuid
import tempfile
import datetime
import img2pdf
import boto3
from bson import ObjectId
from typing import List, Optional
from fastapi import HTTPException
from app.core.config import (
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    S3_BUCKET_NAME,
    S3_REGION_NAME
)
from botocore.config import Config

class PDFUtil:
    def __init__(self, db):
        self.db = db
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=S3_REGION_NAME,
            config=Config(signature_version='s3v4')
        )

    async def create_pdf_from_images(
        self,
        user_id: ObjectId,
        storage_id: str,
        image_paths: List[str],
        pdf_title: str,
        primary_file_id: Optional[str] = None,
        storage_type: str = "pdfs"
    ) -> dict:
        """
        이미지들을 PDF로 변환하고 S3에 저장합니다.

        Args:
            user_id (ObjectId): 사용자 ID
            storage_id (str): 보관함 ID
            image_paths (List[str]): 이미지 파일 경로 목록
            pdf_title (str): PDF 파일 제목
            primary_file_id (Optional[str]): 대표 파일 ID
            storage_type (str): 저장 경로 타입

        Returns:
            dict: 생성된 PDF 파일 정보
        """
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                pdf_path = os.path.join(temp_dir, "combined.pdf")
                with open(pdf_path, "wb") as f:
                    f.write(img2pdf.convert(image_paths))

                pdf_id = str(uuid.uuid4())
                s3_key = f"{storage_type}/{user_id}/{pdf_id}.pdf"

                with open(pdf_path, "rb") as f:
                    self.s3_client.upload_fileobj(
                        f,
                        S3_BUCKET_NAME,
                        s3_key,
                        ExtraArgs={'ContentType': 'application/pdf'}
                    )

                now = datetime.datetime.now(datetime.UTC)
                pdf_doc = {
                    "storage_id": ObjectId(storage_id),
                    "user_id": user_id,
                    "title": pdf_title,
                    "s3_key": s3_key,
                    "created_at": now,
                    "updated_at": now,
                    "mime_type": "application/pdf",
                    "file_size": os.path.getsize(pdf_path)
                }

                if primary_file_id:
                    pdf_doc.update({
                        "primary_file_id": ObjectId(primary_file_id),
                        "is_primary": False
                    })

                result = await self.db.files.insert_one(pdf_doc)
                return {
                    "file_id": str(result.inserted_id),
                    "s3_key": s3_key
                }

        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"PDF 생성 실패: {str(e)}"
            )