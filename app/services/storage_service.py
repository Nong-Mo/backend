# app/services/storage_service.py
from fastapi import HTTPException
from app.schemas.storage import (
    StorageInfo,
    StorageListResponse,
    StorageDetailResponse,
    FileDetail,
    FileDetailResponse
)
from bson import ObjectId
from botocore.config import Config
from app.core.config import (
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    S3_REGION_NAME,
    S3_BUCKET_NAME
)
import boto3, datetime

class StorageService:
    def __init__(self, db):
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
    async def create(cls, db):
        return cls(db)

    async def get_storage_list(self, user_email: str) -> StorageListResponse:
        """사용자의 전체 보관함 목록을 조회합니다."""
        try:
            user = await self.users_collection.find_one({"email": user_email})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

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
        """특정 보관함의 상세 정보를 조회합니다."""
        try:
            user = await self.users_collection.find_one({"email": user_email})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            storage = await self.db.storages.find_one({
                "user_id": user["_id"],
                "name": storage_name
            })
            if not storage:
                raise HTTPException(status_code=404, detail="Storage not found")

            file_list = []
            cursor = self.db.files.find({
                "storage_id": storage["_id"],
                "is_primary": True
            })

            async for file in cursor:
                file_list.append(FileDetail(
                    fileID=str(file["_id"]),
                    fileName=file["title"],
                    uploadDate=file["created_at"],
                    recentDate=file["recented_at"]
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
        """파일의 상세 정보를 조회합니다."""
        try:
            user = await self.users_collection.find_one({"email": user_email})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            file = await self.db.files.find_one({
                "_id": ObjectId(file_id),
                "user_id": user["_id"]
            })
            if not file:
                raise HTTPException(status_code=404, detail="File not found")

            file_url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': S3_BUCKET_NAME,
                    'Key': file['s3_key']
                },
                ExpiresIn=3600
            )

            file_type = "audio"
            if file.get("mime_type") == "application/pdf":
                file_type = "pdf"
            elif file.get("mime_type", "").startswith("image/"):
                file_type = "image"

            related_file = None
            if file.get("is_primary"):
                related_file = await self.db.files.find_one({
                    "primary_file_id": file["_id"]
                })
            elif file.get("primary_file_id"):
                related_file = await self.db.files.find_one({
                    "_id": file["primary_file_id"]
                })

            related_file_info = None
            if related_file:
                related_file_url = self.s3_client.generate_presigned_url(
                    'get_object',
                    Params={
                        'Bucket': S3_BUCKET_NAME,
                        'Key': related_file['s3_key']
                    },
                    ExpiresIn=3600
                )
                related_file_info = {
                    "fileUrl": related_file_url,
                    "fileType": "pdf" if file_type == "audio" else "audio"
                }

            return FileDetailResponse(
                fileID=str(file["_id"]),
                fileName=file["title"],
                uploadDate=file["created_at"],
                fileUrl=file_url,
                contents=file.get("contents"),
                fileType=file_type,
                relatedFile=related_file_info
            )

        except HTTPException as e:
            raise e
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch file detail: {str(e)}"
            )

    async def delete_file(self, user_email: str, file_id: str):
        """파일을 삭제합니다."""
        try:
            user = await self.users_collection.find_one({"email": user_email})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            file = await self.db["files"].find_one({"_id": ObjectId(file_id)})
            if not file:
                raise HTTPException(status_code=404, detail="File not found")

            if file["user_id"] != user["_id"]:
                raise HTTPException(
                    status_code=403,
                    detail="You do not have permission to delete this file"
                )

            self.s3_client.delete_object(
                Bucket=S3_BUCKET_NAME,
                Key=file['s3_key']
            )

            if file.get("is_primary"):
                related_file = await self.db.files.find_one({"primary_file_id": file["_id"]})
                if related_file:
                    self.s3_client.delete_object(
                        Bucket=S3_BUCKET_NAME,
                        Key=related_file['s3_key']
                    )
                    await self.db["files"].delete_one({"_id": related_file["_id"]})

            await self.db["files"].delete_one({"_id": ObjectId(file_id)})
            return {"message": "File deleted successfully"}

        except HTTPException as e:
            raise e
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to delete file: {str(e)}"
            )
            
    async def update_recent_date(self, user_email: str, file_id: str):
        """파일의 최근 열람일을 갱신합니다."""
        try:
            user = await self.users_collection.find_one({"email": user_email})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            file = await self.db["files"].find_one({"_id": ObjectId(file_id)})
            if not file:
                raise HTTPException(status_code=404, detail="File not found")

            if file["user_id"] != user["_id"]:
                raise HTTPException(
                    status_code=403,
                    detail="You do not have permission to update this file"
                )

            await self.db["files"].update_one(
                {"_id": ObjectId(file_id)},
                {"$set": {"recented_at": datetime.datetime.now()}}
            )

        except HTTPException as e:
            raise e
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to update recent date: {str(e)}"
            )