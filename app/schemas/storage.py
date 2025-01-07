from datetime import datetime
from pydantic import BaseModel
from typing import List

class StorageInfo(BaseModel):
    storageName: str # 보관함 이름 (예: "책 보관함", "편지 보관함" 등)
    fileCount: int # 해당 보관함에 저장된 파일 수

class StorageListResponse(BaseModel):
    nickname: str # 사용자 닉네임
    storageList: List[StorageInfo] # StorageInfo 객체들의 리스트

class FileDetail(BaseModel):
    fileID: str # 파일 ID
    fileName: str # 파일 이름
    uploadDate: datetime # 업로드 날짜

class StorageDetailResponse(BaseModel):
    storageName: str # 보관함 이름
    fileList: List[FileDetail] # FileDetail 객체들의 리스트

class AudioFileDetail(BaseModel):
    fileID: str
    fileName: str
    uploadDate: datetime
    audioUrl: str  # S3에서 가져온 오디오 파일 URL
    contents: str  # 파일의 텍스트 내용