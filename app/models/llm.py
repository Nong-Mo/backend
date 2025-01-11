from typing import TypedDict, Optional

class FileSearchResult(TypedDict):
    found: bool  # True/False로 검색 결과 표시
    message: str  # 사용자에게 보여줄 메시지
    data: Optional[dict]  # 파일 정보