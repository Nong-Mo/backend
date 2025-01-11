# app/models/llm.py
from typing import TypedDict, Optional, Literal

# 가능한 응답 타입을 Literal로 정의
ResponseType = Literal["file_found", "chat", "error"]

class FileSearchResult(TypedDict):
    type: ResponseType  # 응답 타입 ("file_found", "chat", "error")
    message: str        # 사용자에게 보여줄 메시지
    data: Optional[dict]  # 파일 정보 (file_found일 때만 사용)