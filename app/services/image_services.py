import os
import uuid
from typing import List
from fastapi import UploadFile, HTTPException, status
from app.models.image import ImageMetadata, ImageDocument
from app.core.config import NAVER_CLOVA_OCR_API_URL, NAVER_CLOVA_OCR_SECRET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET_NAME
import boto3
import requests
from datetime import datetime
import shutil

s3 = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY
)

class ImageService:
    async def process_images(self, title: str, files: List[UploadFile]):
        file_id = str(uuid.uuid4())
        upload_dir = f"/tmp/{file_id}"
        os.makedirs(upload_dir, exist_ok=True)

        processed_files = []
        try:
            for file in files:
                file_path = os.path.join(upload_dir, file.filename)
                # 파일을 로컬에 저장
                with open(file_path, "wb") as f:
                    content = await file.read()
                    f.write(content)

                # 네이버 CLOVA OCR 호출 및 결과 처리
                text, pdf_file = self._call_clova_ocr(file_path)

                # S3에 PDF 업로드 (PDF 경로 설정)
                s3_key_prefix = f"{file_id}/{file.filename}"
                s3.upload_file(pdf_file, S3_BUCKET_NAME, f"{s3_key_prefix}.pdf")

                # 이미지 파일 메타데이터 저장
                processed_files.append(ImageMetadata(
                    filename=file.filename,
                    content_type=file.content_type,
                    size=len(content)
                ))

            # 이미지 문서 반환
            image_doc = ImageDocument(
                title=title,
                file_id=file_id,
                processed_files=processed_files,
                created_at=datetime.utcnow().isoformat()
            )
            return image_doc

        except requests.exceptions.RequestException as e:
            # OCR 외부 API에서 오류 발생시 처리
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"External API Error: {str(e)}"
            )
        finally:
            # 업로드 후 임시 디렉토리 삭제
            shutil.rmtree(upload_dir)

    def _call_clova_ocr(self, image_path):
        headers = {'X-OCR-SECRET': NAVER_CLOVA_OCR_SECRET}
        # 이미지 파일을 열어 CLOVA OCR에 전송
        with open(image_path, 'rb') as file:
            files = {'file': file}
            response = requests.post(NAVER_CLOVA_OCR_API_URL, headers=headers, files=files)
        response.raise_for_status()

        data = response.json()

        # OCR 텍스트 및 PDF 반환
        text = " ".join(field['inferText'] for field in data['images'][0]['fields'])
        pdf_file = f"{image_path}.pdf"  # OCR API에서 반환된 PDF 파일 경로
        return text, pdf_file