from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.schemas.storage import StorageInfo, StorageListResponse, StorageDetailResponse, FileDetail, AudioFileDetail
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
            cursor = self.db.files.find({"storage_id": storage["_id"]})

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

    async def get_file_detail(self, user_email: str, file_id: str) -> AudioFileDetail:
        """
        특정 파일의 상세 정보와 오디오 URL을 조회합니다.

        Args:
            user_email (str): 사용자 이메일
            file_id (str): 파일 ID

        Returns:
            AudioFileDetail: 파일 상세 정보와 오디오 URL
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

            # 3. S3 미리 서명된 URL 생성 (1시간 유효)
            audio_url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': S3_BUCKET_NAME,
                    'Key': file['s3_key']
                },
                ExpiresIn=3600  # 1시간
            )

            return AudioFileDetail(
                fileID=str(file["_id"]),
                fileName=file["title"],
                uploadDate=file["created_at"],
                audioUrl=audio_url,
                contents=file["contents"]
            )

        except HTTPException as e:
            raise e
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch file detail: {str(e)}"
            )