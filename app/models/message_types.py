# app/models/message_types.py
from enum import Enum

class MessageType(Enum):
    GENERAL = "general"           # 일반 대화
    BOOK_STORY = "book_story"     # 책 컨텐츠
    RECEIPT_RAW = "receipt_raw"   # 영수증 OCR 원본 데이터
    RECEIPT_SUMMARY = "receipt_summary"  # LLM이 정리한 영수증 분석 결과