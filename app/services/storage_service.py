from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.config import db
from app.schemas.storage import StorageInfo, StorageListResponse
from typing import List

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
        사용자의 보관함 목록과 각 보관함별 파일 수를 조회합니다.

        동작 과정:
        1. 사용자 이메일로 사용자 정보를 조회
        2. MongoDB Aggregation을 사용하여 보관함별 파일 수를 집계
        3. 결과를 Pydantic 모델에 맞춰 변환하여 반환

        Args:
            user_email (str): 조회할 사용자의 이메일

        Returns:
            StorageListResponse: {
                "nickname": "사용자닉네임",
                "storageList": [
                    {"storageName": "보관함1", "fileCount": 5},
                    {"storageName": "보관함2", "fileCount": 3},
                    ...
                ]
            }

        Raises:
            HTTPException(404): 사용자를 찾을 수 없는 경우
            HTTPException(500): 기타 서버 오류 발생 시
        """
        try:
            # 1. 사용자 정보 조회
            user = await self.users_collection.find_one({"email": user_email})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # 2. MongoDB Aggregation Pipeline 구성
            pipeline = [
                # 2-1. 해당 사용자의 데이터만 필터링
                {"$match": {"user_email": user_email}},
                
                # 2-2. storageName을 기준으로 그룹화하고 파일 수 집계
                {"$group": {
                    "_id": "$storageName",     # 그룹화 기준 필드
                    "fileCount": {"$sum": 1}   # 각 그룹의 문서 수 합산
                }}
            ]
            
            # 3. Aggregation 실행 및 결과 처리
            storage_cursor = self.images_collection.aggregate(pipeline)
            storage_list = []
            
            # 4. 커서를 순회하며 결과 변환
            async for storage in storage_cursor:
                storage_list.append(StorageInfo(
                    storageName=storage["_id"],    # 그룹화 키(_id)가 보관함 이름
                    fileCount=storage["fileCount"] # 집계된 파일 수
                ))

            # 5. 최종 응답 생성
            return StorageListResponse(
                nickname=user["nickname"],
                storageList=storage_list
            )

        except Exception as e:
            # 예상치 못한 오류 처리
            raise HTTPException(
                status_code=500, 
                detail=f"Failed to fetch storage list: {str(e)}"
            ) 