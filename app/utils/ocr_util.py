# app/utils/ocr_util.py
import uuid
import time
import json
import requests
import logging
from fastapi import HTTPException, UploadFile
from app.core.config import (
    NAVER_CLOVA_OCR_SECRET,
    NAVER_CLOVA_OCR_API_URL,
    NAVER_CLOVA_RECEIPT_OCR_SECRET,
    NAVER_CLOVA_RECEIPT_OCR_API_URL
)
from app.core.exceptions import OCRProcessingError, DataParsingError

# 파일 크기 제한
MAX_FILE_SIZE = 10 * 1024 * 1024
MIN_FILE_SIZE = 1

logger = logging.getLogger(__name__)

async def process_ocr(file: UploadFile) -> list:
    """
    일반 OCR 처리를 수행합니다.

    Args:
        file (UploadFile): OCR 처리할 이미지 파일

    Returns:
        list: 추출된 텍스트 목록
    """
    try:
        file.file.seek(0)
        contents = await file.read()
        file_size = len(contents)

        if file_size < MIN_FILE_SIZE or file_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"파일 크기가 허용된 범위를 벗어났습니다. 최소 {MIN_FILE_SIZE}바이트, 최대 {MAX_FILE_SIZE // (1024 * 1024)}MB"
            )

        request_json = {
            'images': [{
                'format': file.content_type.split('/')[1],
                'name': file.filename
            }],
            'requestId': str(uuid.uuid4()),
            'version': 'V2',
            'timestamp': int(round(time.time() * 1000))
        }

        payload = {'message': json.dumps(request_json).encode('UTF-8')}
        files = [('file', (file.filename, contents, file.content_type))]
        headers = {'X-OCR-SECRET': NAVER_CLOVA_OCR_SECRET}

        response = requests.request("POST", NAVER_CLOVA_OCR_API_URL, headers=headers, data=payload, files=files)
        response.raise_for_status()

        response_json = response.json()
        extracted_texts = []
        for image in response_json.get('images', []):
            for field in image.get('fields', []):
                text = field.get('inferText', '')
                extracted_texts.append(text)

        return extracted_texts

    except requests.exceptions.HTTPError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"HTTP 오류: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OCR 처리 오류: {str(e)}")

async def process_receipt_ocr(file: UploadFile) -> dict:
    """영수증 OCR 처리를 수행합니다."""
    try:
        file.file.seek(0)
        contents = await file.read()
        encoded_message = json.dumps({
            'version': 'V2',
            'requestId': str(uuid.uuid4()),
            'timestamp': int(round(time.time() * 1000)),
            'images': [{
                'format': file.content_type.split('/')[1],
                'name': file.filename
            }]
        })

        try:
            response = requests.post(
                NAVER_CLOVA_RECEIPT_OCR_API_URL,
                headers={'X-OCR-SECRET': NAVER_CLOVA_RECEIPT_OCR_SECRET},
                data={'message': encoded_message},
                files={'file': (file.filename, contents, file.content_type)}
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise OCRProcessingError(f"API 호출 실패: {e.response.text}")
        except json.JSONDecodeError as e:
            raise DataParsingError(f"OCR 결과 파싱 실패: {str(e)}")
        except Exception as e:
            raise OCRProcessingError(f"알 수 없는 OCR 오류: {str(e)}")

    except Exception as e:
        if isinstance(e, (OCRProcessingError, DataParsingError)):
            raise e
        raise OCRProcessingError(f"OCR 처리 중 오류 발생: {str(e)}")