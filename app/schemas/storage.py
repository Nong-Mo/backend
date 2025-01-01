from pydantic import BaseModel
from typing import List

class StorageInfo(BaseModel):
    storageName: str # 보관함 이름 (예: "책 보관함", "편지 보관함" 등)
    fileCount: int # 해당 보관함에 저장된 파일 수

class StorageListResponse(BaseModel):
    nickname: str # 사용자 닉네임
    storageList: List[StorageInfo] # StorageInfo 객체들의 리스트

