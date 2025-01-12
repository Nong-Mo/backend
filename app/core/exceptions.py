from fastapi import HTTPException

class OCRProcessingError(HTTPException):
    """OCR 처리 중 발생하는 예외"""
    def __init__(self, detail: str):
        super().__init__(status_code=500, detail=f"OCR 처리 오류: {detail}")

class DataParsingError(HTTPException):
    """데이터 파싱 중 발생하는 예외"""
    def __init__(self, detail: str):
        super().__init__(status_code=400, detail=f"데이터 파싱 오류: {detail}")

class PDFGenerationError(HTTPException):
    """PDF 생성 중 발생하는 예외"""
    def __init__(self, detail: str):
        super().__init__(status_code=500, detail=f"PDF 생성 오류: {detail}")

class StorageError(HTTPException):
    """저장소 관련 예외"""
    def __init__(self, detail: str, status_code: int = 500):
        super().__init__(status_code=status_code, detail=f"저장소 오류: {detail}")

# 로깅 설정 강화
import logging
import sys

def setup_logger(name: str) -> logging.Logger:
    """애플리케이션 로거 설정"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 콘솔 핸들러
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger