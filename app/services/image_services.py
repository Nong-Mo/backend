import os
import urllib
import uuid
from typing import List
from fastapi import UploadFile, HTTPException, status, Depends, Header
from app.models.image import ImageMetadata, ImageDocument
from app.core.config import (
    NAVER_CLOVA_OCR_API_URL, NAVER_CLOVA_OCR_SECRET,
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET_NAME,
    SECRET_KEY, ALGORITHM, NCP_TTS_API_URL, NCP_CLIENT_ID,
    NCP_CLIENT_SECRET, S3_REGION_NAME
)
import boto3
import requests
import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import shutil
from jose import jwt
from jose.exceptions import JWTError
from fastapi.security import OAuth2PasswordBearer
import time
import json
import urllib.parse
import urllib.request
import ssl
import logging
import asyncio

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 10 * 1024 * 1024
MIN_FILE_SIZE = 1

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


async def verify_jwt(token: str = Header(...)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=ALGORITHM)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    return user_id


class ImageService:
    def __init__(self, mongodb_client: AsyncIOMotorClient):
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=S3_REGION_NAME
        )
        self.db = mongodb_client
        self.storage_collection = self.db.storages
        self.files_collection = self.db.files

    async def create_or_update_storage(self, user_id: str, title: str, file_count: int) -> str:
        """보관함 생성 또는 업데이트"""
        # 먼저 이메일로 사용자 조회
        user = await self.db["users"].find_one({"email": user_id})
        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found"
            )
        
        user_id = user["_id"]  # 실제 ObjectId 가져오기
        now = datetime.datetime.now(datetime.UTC)

        storage = await self.storage_collection.find_one({
            "user_id": user_id,  # 이미 ObjectId 타입
            "name": title
        })

        if storage:
            await self.storage_collection.update_one(
                {"_id": storage["_id"]},
                {
                    "$inc": {"file_count": file_count},
                    "$set": {"updated_at": now}
                }
            )
            return str(storage["_id"])
        else:
            result = await self.storage_collection.insert_one({
                "user_id": user_id,  # 이미 ObjectId 타입
                "name": title,
                "file_count": file_count,
                "created_at": now,
                "updated_at": now
            })
            return str(result.inserted_id)

    async def save_file_metadata(self, storage_id: str, user_id: str, file_info: dict) -> str:
        """파일 메타데이터를 MongoDB에 저장"""
        # 먼저 이메일로 사용자 조회
        user = await self.db["users"].find_one({"email": user_id})
        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found"
            )
        
        now = datetime.datetime.now(datetime.UTC)

        file_doc = {
            "storage_id": ObjectId(storage_id),
            "user_id": user["_id"],  # ObjectId 변환 대신 user._id 사용
            "filename": file_info["filename"],
            "s3_key": file_info["s3_key"],
            "contents": file_info["contents"],
            "file_size": file_info["file_size"],
            "mime_type": file_info["mime_type"],
            "created_at": now,
            "updated_at": now
        }

        result = await self.files_collection.insert_one(file_doc)
        return str(result.inserted_id)

    async def process_images(self, title: str, files: List[UploadFile], user_id: str = Depends(verify_jwt)):
        # user_id는 이메일 형태로 전달됨
        # 사용자 정보 조회
        user = await self.db["users"].find_one({"email": user_id})
        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found"
            )
        
        file_id = str(uuid.uuid4())
        upload_dir = f"/tmp/{user_id}/{file_id}"
        os.makedirs(upload_dir, exist_ok=True)

        processed_files = []
        storage_id = None

        try:
            # Storage 생성 시 실제 user_id(ObjectId) 전달
            storage_id = await self.create_or_update_storage(
                user_id=user_id,  # 이메일 전달
                title=title,
                file_count=len(files)
            )

            for file in files:
                file_path = os.path.join(upload_dir, file.filename)
                try:
                    content = await file.read()
                    if not content:
                        raise HTTPException(status_code=400, detail=f"Empty file: {file.filename}")

                    with open(file_path, "wb") as f:
                        f.write(content)

                    # OCR 텍스트 추출
                    text = await self._call_clova_ocr(file)
                    combined_text = " ".join(text)

                    # TTS 변환 및 S3 업로드
                    s3_key = await self._call_naver_tts(combined_text, file.filename, title)

                    # Files 컬렉션에 저장할 메타데이터 준비
                    file_info = {
                        "filename": file.filename,
                        "s3_key": s3_key,
                        "contents": combined_text,  # OCR로 추출한 텍스트
                        "file_size": len(content),
                        "mime_type": file.content_type
                    }

                    # Files 컬렉션에 메타데이터 저장
                    file_id = await self.save_file_metadata(
                        storage_id=storage_id,
                        user_id=user_id,
                        file_info=file_info
                    )

                    processed_files.append(ImageMetadata(
                        filename=file.filename,
                        content_type=file.content_type,
                        size=len(content)
                    ))

                except (IOError, ValueError) as e:
                    raise HTTPException(status_code=400, detail=f"Failed to process file: {e}")

            image_doc = ImageDocument(
                title=title,
                file_id=file_id,
                processed_files=processed_files,
                created_at=datetime.datetime.now(datetime.UTC).isoformat()
            )
            return image_doc

        except Exception as e:
            # 에러 발생 시 storage_id가 있다면 해당 storage 삭제
            if storage_id:
                await self.storage_collection.delete_one({"_id": ObjectId(storage_id)})
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
                lambda: urllib.request.urlopen(request, context=ssl_context).read(), *()
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
                ),
                *()
            )

            return s3_key  # s3_key만 반환하도록 수정

        except Exception as e:
            logger.error(f"TTS Error: {str(e)}")
            raise HTTPException(status_code=500, detail=f"TTS 생성 실패: {str(e)}")