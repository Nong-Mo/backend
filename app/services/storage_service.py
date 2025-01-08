from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.schemas.storage import StorageInfo, StorageListResponse, StorageDetailResponse, FileDetail, PDFConversionResponse, FileDetailResponse
from typing import List
from bson import ObjectId
from botocore.config import Config
from app.core.config import (
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    S3_REGION_NAME,
    S3_BUCKET_NAME
)
import boto3
import img2pdf
import tempfile
import os
import uuid
import datetime


class StorageService:
    """
    사용자의 보관함 정보를 관리하는 서비스 클래스
    MongoDB와 상호작용하여 보관함 관련 데이터를 처리합니다.
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        """
        데이터베이스 의존성을 주입받는 생성자

        Args:
            db: AsyncIOMotorDatabase - MongoDB 데이터베이스 인스턴스
        """
        self.db = db
        self.users_collection = db["users"]
        self.images_collection = db["images"]
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=S3_REGION_NAME,
            config=Config(signature_version='s3v4')
        )

    @classmethod
    async def create(cls, db: AsyncIOMotorDatabase):
        """
        서비스 인스턴스를 생성하는 팩토리 메서드

        Args:
            db: AsyncIOMotorDatabase - MongoDB 데이터베이스 인스턴스
        """
        return cls(db)

    async def get_storage_list(self, user_email: str) -> StorageListResponse:
        """
        사용자의 전체 보관함 목록을 조회합니다.

        Args:
            user_email (str): 사용자 이메일

        Returns:
            StorageListResponse: 사용자의 닉네임과 보관함 목록 정보
        """
        try:
            # 1. 사용자 정보 조회
            user = await self.users_collection.find_one({"email": user_email})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # 2. 사용자의 보관함 목록 조회
            storage_list = []
            cursor = self.db.storages.find({"user_id": user["_id"]})

            async for storage in cursor:
                storage_list.append(StorageInfo(
                    storageName=storage["name"],
                    fileCount=storage["file_count"]
                ))

            return StorageListResponse(
                nickname=user["nickname"],
                storageList=storage_list
            )

        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch storage list: {str(e)}"
            )

    async def get_storage_detail(self, user_email: str, storage_name: str) -> StorageDetailResponse:
        """
        특정 보관함의 상세 정보를 조회합니다.

        Args:
            user_email (str): 사용자 이메일
            storage_name (str): 조회할 보관함 이름

        Returns:
            StorageDetailResponse: 보관함의 상세 정보와 파일 목록
        """
        try:
            # 1. 사용자 정보 조회
            user = await self.users_collection.find_one({"email": user_email})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # 2. 보관함 정보 조회
            storage = await self.db.storages.find_one({
                "user_id": user["_id"],
                "name": storage_name
            })
            if not storage:
                raise HTTPException(status_code=404, detail="Storage not found")

            # 3. 보관함의 파일 목록 조회
            file_list = []
            cursor = self.db.files.find({
                "storage_id": storage["_id"],
                "is_primary": True # primary 파일만 조회
            })

            async for file in cursor:
                file_list.append(FileDetail(
                    fileID=str(file["_id"]),
                    fileName=file["title"],
                    uploadDate=file["created_at"]
                ))

            return StorageDetailResponse(
                storageName=storage_name,
                fileList=file_list
            )

        except HTTPException as e:
            raise e
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch storage detail: {str(e)}"
            )

    async def get_file_detail(self, user_email: str, file_id: str) -> FileDetailResponse:
        """
        특정 파일의 상세 정보와 URL을 조회합니다.
        primary 파일의 경우 연관된 파일(PDF)도 함께 조회합니다.

        Args:
            user_email (str): 사용자 이메일
            file_id (str): 파일 ID

        Returns:
            FileDetailResponse: 파일 상세 정보, URL 및 연관 파일 정보
        """
        try:
            # 1. 사용자 정보 조회
            user = await self.users_collection.find_one({"email": user_email})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # 2. 파일 정보 조회
            file = await self.db.files.find_one({
                "_id": ObjectId(file_id),
                "user_id": user["_id"]
            })
            if not file:
                raise HTTPException(status_code=404, detail="File not found")

            # 3. S3 미리 서명된 URL 생성
            file_url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': S3_BUCKET_NAME,
                    'Key': file['s3_key']
                },
                ExpiresIn=3600
            )

            # 4. 파일 타입 결정
            file_type = "audio"  # 기본값
            if file.get("mime_type") == "application/pdf":
                file_type = "pdf"
            elif file.get("mime_type", "").startswith("image/"):
                file_type = "image"

            # 5. 연관된 파일 조회
            related_file = None
            if file.get("is_primary"):
                related_file = await self.db.files.find_one({
                    "primary_file_id": file["_id"]
                })
            elif file.get("primary_file_id"):
                related_file = await self.db.files.find_one({
                    "_id": file["primary_file_id"]
                })

            # 6. 연관 파일 URL 생성
            related_file_url = None
            if related_file:
                related_file_url = self.s3_client.generate_presigned_url(
                    'get_object',
                    Params={
                        'Bucket': S3_BUCKET_NAME,
                        'Key': related_file['s3_key']
                    },
                    ExpiresIn=3600
                )

            return FileDetailResponse(
                fileID=str(file["_id"]),
                fileName=file["title"],
                uploadDate=file["created_at"],
                fileUrl=file_url,
                contents=file.get("contents"),
                fileType=file_type,
                relatedFile={
                    "fileUrl": related_file_url,
                    "fileType": "pdf" if file_type == "audio" else "audio"
                } if related_file else None
            )

        except HTTPException as e:
            raise e
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch file detail: {str(e)}"
            )

    async def convert_to_pdf(self, user_email: str, file_ids: List[str], pdf_title: str) -> PDFConversionResponse:
        """
        선택된 이미지들을 PDF로 변환합니다.

        Args:
            user_email (str): 사용자 이메일
            file_ids (List[str]): 변환할 이미지 파일 ID 목록
            pdf_title (str): 사용자가 지정한 PDF 파일 이름

        Returns:
            PDFConversionResponse: PDF 파일 정보
        """
        try:
            # 1. 사용자 정보 조회
            user = await self.users_collection.find_one({"email": user_email})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # 2. 파일 정보 조회 및 이미지 다운로드
            image_files = []
            with tempfile.TemporaryDirectory() as temp_dir:
                for file_id in file_ids:
                    file = await self.db.files.find_one({
                        "_id": ObjectId(file_id),
                        "user_id": user["_id"]
                    })
                    if not file:
                        raise HTTPException(status_code=404, detail=f"File {file_id} not found")

                    # S3에서 이미지 다운로드
                    temp_image_path = os.path.join(temp_dir, f"{file_id}.jpg")
                    self.s3_client.download_file(
                        S3_BUCKET_NAME,
                        file['s3_key'],
                        temp_image_path
                    )
                    image_files.append(temp_image_path)

                # 3. PDF 생성
                pdf_path = os.path.join(temp_dir, "combined.pdf")
                with open(pdf_path, "wb") as f:
                    f.write(img2pdf.convert(image_files))

                # 4. PDF를 S3에 업로드
                pdf_id = str(uuid.uuid4())
                s3_key = f"pdfs/{user['_id']}/{pdf_id}.pdf"
                
                with open(pdf_path, "rb") as f:
                    self.s3_client.upload_fileobj(
                        f,
                        S3_BUCKET_NAME,
                        s3_key,
                        ExtraArgs={'ContentType': 'application/pdf'}
                    )

                # 5. PDF 파일 메타데이터 저장
                now = datetime.datetime.now(datetime.UTC)
                pdf_doc = {
                    "user_id": user["_id"],
                    "title": pdf_title,
                    "s3_key": s3_key,
                    "source_files": file_ids,
                    "created_at": now,
                    "updated_at": now,
                    "mime_type": "application/pdf",
                    "file_size": os.path.getsize(pdf_path)
                }
                
                result = await self.db.files.insert_one(pdf_doc)
                
                # 6. 미리 서명된 URL 생성
                pdf_url = self.s3_client.generate_presigned_url(
                    'get_object',
                    Params={
                        'Bucket': S3_BUCKET_NAME,
                        'Key': s3_key
                    },
                    ExpiresIn=3600  # 1시간
                )

                return PDFConversionResponse(
                    fileID=str(result.inserted_id),
                    pdfUrl=pdf_url
                )

        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to convert images to PDF: {str(e)}"
            )

    async def create_pdf_from_images(
        self, 
        user_id: ObjectId, 
        storage_id: str,
        image_paths: List[str], 
        pdf_title: str,
        primary_file_id: str  # 추가된 파라미터
    ) -> dict:
        """
        이미지들을 PDF로 변환하고 저장합니다.

        Args:
            user_id (ObjectId): 사용자 ID
            storage_id (str): 보관함 ID
            image_paths (List[str]): 변환할 이미지 파일 경로 목록
            pdf_title (str): PDF 파일 제목
            primary_file_id (str): 대표 파일(MP3)의 ID
        Returns:
            dict: PDF 파일 메타데이터
        """
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                # 1. PDF 생성
                pdf_path = os.path.join(temp_dir, "combined.pdf")
                with open(pdf_path, "wb") as f:
                    f.write(img2pdf.convert(image_paths))

                # 2. PDF를 S3에 업로드
                pdf_id = str(uuid.uuid4())
                s3_key = f"pdfs/{user_id}/{pdf_id}.pdf"
                
                with open(pdf_path, "rb") as f:
                    self.s3_client.upload_fileobj(
                        f,
                        S3_BUCKET_NAME,
                        s3_key,
                        ExtraArgs={'ContentType': 'application/pdf'}
                    )

                # 3. PDF 파일 메타데이터 저장
                now = datetime.datetime.now(datetime.UTC)
                pdf_doc = {
                    "storage_id": ObjectId(storage_id),
                    "user_id": user_id,
                    "title": pdf_title,  # "(PDF)" 제거
                    "s3_key": s3_key,
                    "created_at": now,
                    "updated_at": now,
                    "mime_type": "application/pdf",
                    "file_size": os.path.getsize(pdf_path),
                    "primary_file_id": ObjectId(primary_file_id),  # MP3 파일과 연결
                    "is_primary": False  # 연관 파일 표시
                }
                
                result = await self.db.files.insert_one(pdf_doc)
                return {
                    "file_id": str(result.inserted_id),
                    "s3_key": s3_key
                }

        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create PDF: {str(e)}"
            )