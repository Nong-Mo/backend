import os
import urllib
import uuid
from typing import List
from fastapi import UploadFile, HTTPException, status, Depends, Header
from app.models.image import ImageMetadata, ImageDocument
from app.core.config import NAVER_CLOVA_OCR_API_URL, NAVER_CLOVA_OCR_SECRET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET_NAME, SECRET_KEY, ALGORITHM, NCP_TTS_API_URL, NCP_CLIENT_ID, NCP_CLIENT_SECRET, S3_REGION_NAME
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
import aiohttp
import urllib.parse
import urllib.request
import ssl
import logging
import asyncio

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 최대 허용 파일 크기: 5MB
MIN_FILE_SIZE = 1

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
    def __init__(self):
        # S3 클라이언트 초기화
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=S3_REGION_NAME
        )

    async def process_images(self, title: str, files: List[UploadFile], user_id: str = Depends(verify_jwt)):
        file_id = str(uuid.uuid4())
        # 사용자 ID를 포함한 업로드 경로 생성
        upload_dir = f"/tmp/{user_id}/{file_id}"
        os.makedirs(upload_dir, exist_ok=True)

        processed_files = []
        '''
        네이버 CLOVA OCR로 텍스트를 추출한 뒤 TTS로 mp3를 생성합니다.
        Args:
            files (list[UploadFile]): 업로드된 파일 목록
        '''
        try:
            for file in files:
                file_path = os.path.join(upload_dir, file.filename)
                try:
                    # 파일 내용을 읽어 변수에 저장
                    content = await file.read()
                    if not content:  # 파일 크기가 0인 경우
                        print(f"[Error] Empty file detected: {file.filename} (Size: 0 bytes)")
                        raise HTTPException(status_code=400, detail=f"Empty file: {file.filename}")

                    # 파일 크기와 콘텐츠 유형 로깅
                    #print(f"Processing file: {file.filename} (Size: {len(content)} bytes)")

                    with open(file_path, "wb") as f:
                        f.write(content)

                except (IOError, ValueError) as e:
                    raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")

                    # 네이버 CLOVA OCR 호출 및 결과 처리 (텍스트만 추출)
                try:
                    #print('file', file)
                    text = await self._call_clova_ocr(file)
                except aiohttp.ClientError as e:
                    raise HTTPException(status_code=500, detail=f"CLOVA OCR API 호출 실패: {e}")

                combined_text = " ".join(text)

                try:
                    await self._call_naver_tts(combined_text, file.filename, title)
                except aiohttp.ClientError as e:
                    raise HTTPException(status_code=500, detail=f"TTS API 호출 실패: {e}")

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
            shutil.rmtree(upload_dir, ignore_errors=True)

    async def _call_clova_ocr(self, file: UploadFile):
        """
        OCR API를 호출하여 이미지에서 텍스트를 추출합니다.
        Args:
            file (UploadFile): 업로드할 이미지 파일
        Returns:
            list: 추출된 텍스트 목록
        """
        #print('file parameter in call_clova_ocr', file)
        try:
            file.file.seek(0)
            contents = await file.read()
            #print('contents 입니다.', contents)
            file_size = len(contents)
            #print('file_size', file_size)
            # 파일 크기 확인
            if file_size < MIN_FILE_SIZE or file_size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=f"파일 크기가 허용된 범위를 벗어났습니다. 최소 {MIN_FILE_SIZE}바이트, 최대 {MAX_FILE_SIZE // (1024 * 1024)}MB 파일만 업로드할 수 있습니다."
                )

            request_json = {
                'images': [
                    {
                        'format': file.content_type.split('/')[1],  # Content-Type에서 동적으로 format 추출
                        'name': file.filename
                    }
                ],
                'requestId': str(uuid.uuid4()),
                'version': 'V2',
                'timestamp': int(round(time.time() * 1000))
            }

            payload = {'message': json.dumps(request_json).encode('UTF-8')}
            files = [
                ('file', (file.filename, contents, file.content_type))  # Content-Type 동적으로 설정
            ]
            headers = {
                'X-OCR-SECRET': NAVER_CLOVA_OCR_SECRET
            }
            response = requests.request("POST", NAVER_CLOVA_OCR_API_URL, headers=headers, data=payload, files=files)
            response.raise_for_status()

            response_json = response.json()

            extracted_texts = []
            for image in response_json.get('images', []):  # 'images' 키가 없을 경우를 대비
                for field in image.get('fields', []):  # 'fields' 키가 없을 경우를 대비
                    text = field.get('inferText', '')  # 'inferText' 키가 없을 경우를 대비
                    extracted_texts.append(text)

            return extracted_texts

        except requests.exceptions.HTTPError as e:
            raise HTTPException(status_code=e.response.status_code,
                                detail=f"HTTP 오류 발생: {e.response.status_code} - {e.response.text}")
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=f"요청 오류 발생: {e}")
        except KeyError as e:
            raise HTTPException(status_code=500, detail=f"JSON 파싱 오류 발생: {e}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"알 수 없는 오류 발생: {e}")

    # 메소드 시그니처 수정
    async def _call_naver_tts(self, text: str, filename: str, title: str):
        try:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            val = {
                "speaker": "nara",
                "volume": "0",
                "speed": "0",
                "pitch": "0",
                "text": text,
                "format": "mp3"
            }

            data = urllib.parse.urlencode(val).encode('utf-8')
            headers = {
                "X-NCP-APIGW-API-KEY-ID": NCP_CLIENT_ID,
                "X-NCP-APIGW-API-KEY": NCP_CLIENT_SECRET
            }

            request = urllib.request.Request(NCP_TTS_API_URL, data, headers)

            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: urllib.request.urlopen(request, context=ssl_context).read()
            )

            file_id = str(uuid.uuid4())
            s3_key = f"tts/{filename}/{title}/{file_id}.mp3"

            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.s3_client.put_object(
                    Bucket=S3_BUCKET_NAME,
                    Key=s3_key,
                    Body=response,
                    ContentType='audio/mp3'
                )
            )

            return f"s3://{S3_BUCKET_NAME}/{s3_key}"

        except Exception as e:
            logger.error(f"TTS Error: {str(e)}")
            raise HTTPException(status_code=500, detail=f"TTS 생성 실패: {str(e)}")