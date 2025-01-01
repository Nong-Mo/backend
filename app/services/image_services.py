import os
import uuid
from typing import List
from fastapi import UploadFile, HTTPException, status, Depends, Header
from app.models.image import ImageMetadata, ImageDocument
from app.core.config import NAVER_CLOVA_OCR_API_URL, NAVER_CLOVA_OCR_SECRET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET_NAME, SECRET_KEY, ALGORITHM
import boto3
import botocore
import requests
from datetime import datetime
import shutil
from jose import jwt  # JWT 처리 라이브러리
from jose.exceptions import JWTError
from fastapi.security import OAuth2PasswordBearer
import time
import json

s3 = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# JWT 인증 검증 함수
async def verify_jwt(token: str = Header(...)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=ALGORITHM)
        user_id: str = payload.get("sub")  # 사용자 ID 추출
        if user_id is None:
            raise credentials_exception
        # 여기에서 사용자 정보를 가져오는 로직을 추가할 수 있습니다.
        # 예: user = await get_user_by_id(user_id)
    except JWTError:  # JWT 검증 실패 시 예외 처리
        raise credentials_exception
    return user_id  # 사용자 ID 반환


class ImageService:
    async def process_images(self, title: str, files: List[UploadFile], user_id: str = Depends(verify_jwt)):
        file_id = str(uuid.uuid4())
        # 사용자 ID를 포함한 업로드 경로 생성
        upload_dir = f"/tmp/{user_id}/{file_id}"
        os.makedirs(upload_dir, exist_ok=True)

        processed_files = []
        try:
            for file in files:
                file_path = os.path.join(upload_dir, file.filename)
                # 파일을 로컬에 저장
                try:
                    with open(file_path, "wb") as f:
                        content = await file.read()
                        f.write(content)
                except (IOError, ValueError) as e:
                    raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")

                # 네이버 CLOVA OCR 호출 및 결과 처리 (텍스트만 추출)
                text = await self._call_clova_ocr(file)

                # 이미지 파일 메타데이터 저장
                processed_files.append(ImageMetadata(
                    filename=file.filename,
                    content_type=file.content_type,
                    size=len(content)
                ))

            # 이미지 문서 반환
            image_doc = ImageDocument(
                title=title,
                file_id=file_id,
                processed_files=processed_files,
                created_at=datetime.utcnow().isoformat()
            )
            return image_doc

        except HTTPException as e:
            raise e
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")
        finally:
            # 업로드 후 임시 디렉토리 삭제
            shutil.rmtree(upload_dir)

    async def _call_clova_ocr(self, file: UploadFile):
        """
        OCR API를 호출하여 이미지에서 텍스트를 추출합니다.

        Args:
            file (UploadFile): 업로드할 이미지 파일

        Returns:
            list: 추출된 텍스트 목록
        """
        try:
            contents = await file.read()
            request_json = {
                'images': [
                    {
                        'format': 'jpg',  # 이미지 형식에 맞게 수정해야 합니다.
                        'name': file.filename  # 파일 이름을 동적으로 설정합니다.
                    }
                ],
                'requestId': str(uuid.uuid4()),
                'version': 'V2',
                'timestamp': int(round(time.time() * 1000))
            }

            payload = {'message': json.dumps(request_json).encode('UTF-8')}
            files = [
                ('file', (file.filename, contents, 'image/jpeg'))  # 파일 이름을 동적으로 설정합니다.
            ]
            headers = {
                'X-OCR-SECRET': NAVER_CLOVA_OCR_SECRET
            }

            response = requests.request("POST", NAVER_CLOVA_OCR_API_URL, headers=headers, data=payload, files=files)
            response.raise_for_status()  # 에러 발생 시 예외 발생

            extracted_texts = []
            for i in response.json()['images'][0]['fields']:
                text = i['inferText']
                extracted_texts.append(text)

            return extracted_texts

        except Exception as e:
            return {"error": str(e)}