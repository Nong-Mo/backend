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
    NCP_CLIENT_SECRET, S3_REGION_NAME,
    NAVER_CLOVA_RECEIPT_OCR_API_URL, NAVER_CLOVA_RECEIPT_OCR_SECRET
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
from app.services.storage_service import StorageService
import cv2
import numpy as np

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
    # 허용된 보관함 이름 목록
    ALLOWED_STORAGE_NAMES = ["책", "영수증", "굿즈", "필름 사진", "서류", "티켓"]

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

    async def update_storage_count(self, user_id: ObjectId, storage_name: str, file_count: int) -> str:
        """보관함 파일 수 업데이트"""
        storage = await self.storage_collection.find_one({
            "user_id": user_id,
            "name": storage_name
        })

        if not storage:
            raise HTTPException(
                status_code=404,
                detail=f"Storage '{storage_name}' not found for this user"
            )

        now = datetime.datetime.utcnow()
        await self.storage_collection.update_one(
            {"_id": storage["_id"]},
            {
                "$inc": {"file_count": file_count},
                "$set": {"updated_at": now}
            }
        )
        return str(storage["_id"])

    async def save_file_metadata(self, storage_id: str, user_id: ObjectId, file_info: dict) -> str:
        """파일 메타데이터를 MongoDB에 저장"""
        now = datetime.datetime.now(datetime.UTC)

        file_doc = {
            "storage_id": ObjectId(storage_id),
            "user_id": user_id,
            "title": file_info["title"],  # 사용자가 지정한 제목 추가
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

    async def process_images(
        self,
        storage_name: str,
        title: str,
        files: List[UploadFile],
        pages_data: List[dict],  # 각 페이지의 정점 정보 추가
        user_id: str = Depends(verify_jwt)
    ):
        """
        여러 이미지를 하나의 통합된 파일로 처리하는 메서드
        
        Args:
            storage_name: 보관함 이름
            title: 파일 제목
            files: 업로드된 이미지 파일 목록
            pages_data: 각 페이지의 정점 정보 목록
            user_id: 사용자 이메일
        """
        if storage_name not in self.ALLOWED_STORAGE_NAMES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid storage name. Allowed names are: {', '.join(self.ALLOWED_STORAGE_NAMES)}"
            )

        user = await self.db["users"].find_one({"email": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        file_id = str(uuid.uuid4())
        upload_dir = f"/tmp/{user_id}/{file_id}"
        os.makedirs(upload_dir, exist_ok=True)

        storage_id = None
        transformed_image_paths = []  # 변환된 이미지 경로 저장
        combined_text = []

        try:
            # Storage 업데이트 - 파일 하나만 추가
            storage_id = await self.update_storage_count(
                user_id=user["_id"],
                storage_name=storage_name,
                file_count=1  # 여러 이미지를 하나의 파일로 처리하므로 1
            )

            total_size = 0
            for idx, (file, page_data) in enumerate(zip(files, pages_data)):
                try:
                    # 1. 원본 이미지 저장
                    original_path = os.path.join(upload_dir, f"original_{idx}.jpg")
                    content = await file.read()
                    if not content:
                        raise HTTPException(status_code=400, detail=f"Empty file: {file.filename}")

                    with open(original_path, "wb") as f:
                        f.write(content)

                    # 2. 이미지 시점 변환
                    transformed_path = os.path.join(upload_dir, f"transformed_{idx}.jpg")
                    transformed_image = await self._transform_image(
                        original_path,
                        page_data['vertices']
                    )
                    cv2.imwrite(transformed_path, transformed_image)
                    transformed_image_paths.append(transformed_path)

                    # 3. 변환된 이미지로 OCR 수행
                    with open(transformed_path, "rb") as f:
                        transformed_file = UploadFile(
                            file=f,
                            filename=f"transformed_{idx}.jpg",
                            content_type="image/jpeg"
                        )
                        text = await self._call_clova_ocr(transformed_file)
                        combined_text.extend(text)

                    total_size += os.path.getsize(transformed_path)

                except (IOError, ValueError) as e:
                    raise HTTPException(status_code=400, detail=f"Failed to process file: {e}")

            # 모든 텍스트를 하나로 합침
            final_text = " ".join(combined_text)

            # 하나의 TTS 파일 생성 및 S3 업로드
            s3_key = await self._call_naver_tts(final_text, f"combined_{file_id}", storage_name)

            # PDF 생성 (변환된 이미지들로)
            if len(transformed_image_paths) > 0:
                storage_service = StorageService(self.db)
                pdf_result = await storage_service.create_pdf_from_images(
                    user_id=user["_id"],
                    storage_id=storage_id,
                    image_paths=transformed_image_paths,  # 변환된 이미지 경로 사용
                    pdf_title=title
                )
                logger.info(f"PDF created successfully: {pdf_result}")

            # Files 컬렉션에 메타데이터 저장
            file_info = {
                "title": title,
                "filename": f"combined_{file_id}",
                "s3_key": s3_key,
                "contents": final_text,
                "file_size": total_size,
                "mime_type": "multipart/mixed",  # 여러 이미지가 포함된 복합 파일임을 표시
                "original_files": [f.filename for f in files]  # 원본 파일명들 저장
            }

            file_id = await self.save_file_metadata(
                storage_id=storage_id,
                user_id=user["_id"],
                file_info=file_info
            )

            return ImageDocument(
                title=title,
                file_id=file_id,
                processed_files=[ImageMetadata(
                    filename=f"combined_{file_id}",
                    content_type="multipart/mixed",
                    size=total_size
                )],
                created_at=datetime.datetime.now(datetime.UTC).isoformat()
            )

        except Exception as e:
            # Storage 카운트 롤백
            if storage_id:
                await self.storage_collection.update_one(
                    {"_id": ObjectId(storage_id)},
                    {"$inc": {"file_count": -1}}
                )
            raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")
        finally:
            shutil.rmtree(upload_dir, ignore_errors=True)

    async def _transform_image(self, image_path: str, vertices: List[dict]) -> np.ndarray:
        """
        이미지를 크롭하고 시점 변환을 수행하는 메서드
        
        Args:
            image_path: 원본 이미지 경로
            vertices: 4개의 꼭지점 좌표 ({x, y} 형식)
        """
        image = cv2.imread(image_path)
        
        # vertices 리스트를 numpy 배열로 변환
        src_points = np.float32([[v['x'], v['y']] for v in vertices])
        
        width = max(
            np.linalg.norm(src_points[0] - src_points[1]),
            np.linalg.norm(src_points[2] - src_points[3])
        )
        height = max(
            np.linalg.norm(src_points[0] - src_points[3]),
            np.linalg.norm(src_points[1] - src_points[2])
        )
        dst_points = np.float32([
            [0, 0],
            [width - 1, 0],
            [width - 1, height - 1],
            [0, height - 1]
        ])
        
        matrix = cv2.getPerspectiveTransform(src_points, dst_points)
        return cv2.warpPerspective(image, matrix, (int(width), int(height)))

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

    async def call_clova_receipt_ocr(self, file: UploadFile):
        try:
            print(f"API URL: {NAVER_CLOVA_RECEIPT_OCR_API_URL}")
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

            response = requests.post(
                f"{NAVER_CLOVA_RECEIPT_OCR_API_URL}",
                headers={'X-OCR-SECRET': NAVER_CLOVA_RECEIPT_OCR_SECRET},
                data={'message': encoded_message},
                files={'file': (file.filename, contents, file.content_type)}
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"HTTP 오류: {e.response.status_code} - {e.response.text}"
            )
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=f"요청 오류: {e}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"서버 오류: {e}")

    async def process_receipt_ocr(
            self,
            storage_name: str,
            title: str,
            file: UploadFile,
            user_id: str
    ):
        storage_id = None  # 변수 초기화

        try:
            # 사용자 정보 조회
            user = await self.db["users"].find_one({"email": user_id})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # OCR 실행
            ocr_result = await self.call_clova_receipt_ocr(file)

            # S3에 원본 이미지 저장
            file.file.seek(0)
            contents = await file.read()
            file_id = str(uuid.uuid4())
            s3_key = f"receipts/{user_id}/{file_id}/{file.filename}"

            self.s3_client.put_object(
                Bucket=S3_BUCKET_NAME,
                Key=s3_key,
                Body=contents,
                ContentType=file.content_type
            )

            # Storage 업데이트
            storage_id = await self.update_storage_count(
                user_id=user["_id"],
                storage_name=storage_name,
                file_count=1
            )

            # Files 컬렉션에 메타데이터 저장
            file_info = {
                "title": title,
                "filename": file.filename,
                "s3_key": s3_key,
                "contents": ocr_result,  # OCR 결과 저장
                "file_size": len(contents),
                "mime_type": file.content_type
            }

            file_id = await self.save_file_metadata(
                storage_id=storage_id,
                user_id=user["_id"],
                file_info=file_info
            )

            return {
                "file_id": file_id,
                "ocr_result": ocr_result
            }

        except Exception as e:
            # 실패 시 Storage 카운트 롤백
            if storage_id:
                await self.storage_collection.update_one(
                    {"_id": ObjectId(storage_id)},
                    {"$inc": {"file_count": -1}}
                )
            raise e