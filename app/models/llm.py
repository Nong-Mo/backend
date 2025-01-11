from typing import TypedDict, Optional

class FileSearchResult(TypedDict):
    type: str  # 'file_found' or 'not_found' or 'chat'
    message: str
    data: Optional[dict]  # 파일 정보