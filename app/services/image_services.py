import os
import urllib
import uuid
from typing import List, Optional, Dict
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

        now = datetime.datetime.now(datetime.UTC)
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
            "updated_at": now,
            "is_primary": file_info.get("is_primary", False)  # is_primary 필드 추가
        }

        result = await self.files_collection.insert_one(file_doc)
        return str(result.inserted_id)

    async def transform_image(self, image_bytes: bytes, vertices: List[Dict[str, float]]) -> bytes:
        """
        이미지를 정점 정보를 기반으로 변환합니다.
        
        Args:
            image_bytes: 원본 이미지 바이트
            vertices: 4개의 정점 좌표 [{x: float, y: float}, ...]
        
        Returns:
            bytes: 변환된 이미지 바이트
        """
        logger.info(f"Starting image transformation with vertices: {vertices}")
        if len(vertices) != 4:
            logger.error(f"Invalid number of vertices: {len(vertices)}")
            raise HTTPException(
                status_code=400,
                detail="Image transformation requires exactly 4 vertices"
            )

        # 이미지 바이트를 numpy 배열로 변환
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            logger.error("Failed to decode image data")
            raise HTTPException(status_code=400, detail="Invalid image data")

        # 원본 이미지의 정점 좌표
        src_points = np.float32([[v["x"], v["y"]] for v in vertices])
        logger.debug(f"Source points: {src_points}")
        
        # 변환된 이미지의 크기 계산
        width = max(
            np.linalg.norm(src_points[1] - src_points[0]),
            np.linalg.norm(src_points[2] - src_points[3])
        )
        height = max(
            np.linalg.norm(src_points[3] - src_points[0]),
            np.linalg.norm(src_points[2] - src_points[1])
        )
        logger.debug(f"Calculated dimensions - width: {width}, height: {height}")

        # 대상 정점 좌표 설정
        dst_points = np.float32([
            [0, 0],
            [width - 1, 0],
            [width - 1, height - 1],
            [0, height - 1]
        ])
        logger.debug(f"Destination points: {dst_points}")
        
        # 투시 변환 행렬 계산 및 적용
        matrix = cv2.getPerspectiveTransform(src_points, dst_points)
        logger.debug(f"Transformation matrix: {matrix}")
        transformed = cv2.warpPerspective(img, matrix, (int(width), int(height)))
        
        # 변환된 이미지를 바이트로 인코딩
        success, transformed_bytes = cv2.imencode('.jpg', transformed)
        if not success:
            logger.error("Failed to encode transformed image")
            raise HTTPException(status_code=500, detail="Failed to encode transformed image")
            
        logger.info("Image transformation completed successfully")
        return transformed_bytes.tobytes()

    async def process_images(
        self,
        storage_name: str,
        title: str,
        files: List[UploadFile],
        user_id: str,
        vertices_data: Optional[List[Optional[List[Dict[str, float]]]]] = None
    ):
        """
        이미지들을 처리하고 필요한 경우 변환합니다.
        정점 정보가 없으면 원본 이미지를 그대로 처리합니다.
        """
        logger.info(f"Starting process_images with vertices_data: {vertices_data}")
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
        image_paths = []
        combined_text = []

        try:
            # Storage 업데이트 - 파일 하나만 추가
            storage_id = await self.update_storage_count(
                user_id=user["_id"],
                storage_name=storage_name,
                file_count=1  # 여러 이미지를 하나의 파일로 처리하므로 1
            )

            total_size = 0
            combined_text = []
            image_paths = []

            for idx, file in enumerate(files):
                try:
                    content = await file.read()
                    logger.debug(f"Processing file {idx}: {file.filename}")
                    if not content:
                        raise HTTPException(status_code=400, detail=f"Empty file: {file.filename}")

                    # 정점 정보가 있는, 현재 이미지에 대한 vertices가 있는 경우에만 변환
                    if vertices_data and len(vertices_data) > idx and vertices_data[idx] is not None:
                        logger.info(f"Transforming image {idx} with vertices: {vertices_data[idx]}")
                        content = await self.transform_image(content, vertices_data[idx])
                        logger.info(f"Image {idx} transformation completed")
                    else:
                        logger.info(f"Skipping transformation for image {idx} - no vertices data")
                    
                    # 임시 파일 저장
                    file_path = os.path.join(upload_dir, file.filename)
                    with open(file_path, "wb") as f:
                        f.write(content)
                    
                    image_paths.append(file_path)
                    total_size += len(content)

                    # OCR 처리를 위해 파일 포인터 리셋
                    file.file.seek(0)
                    text = await self._call_clova_ocr(file)
                    combined_text.extend(text)

                except (IOError, ValueError) as e:
                    raise HTTPException(status_code=400, detail=f"Failed to process file: {e}")

            # 모든 텍스트를 하나로 합침
            final_text = " ".join(combined_text)

            # 하나의 TTS 파일 생성 및 S3 업로드
            s3_key = await self._call_naver_tts(final_text, f"combined_{file_id}", storage_name)

            # Files 컬렉션에 메타데이터 저장 부분 수정
            file_info = {
                "title": title,
                "filename": f"combined_{file_id}",
                "s3_key": s3_key,
                "contents": final_text,
                "file_size": total_size,
                "mime_type": "audio/mp3",  # MP3를 primary로
                "original_files": [f.filename for f in files],
                "is_primary": True  # 대표 파일 표시
            }

            # MP3 파일 저장
            mp3_file_id = await self.save_file_metadata(
                storage_id=storage_id,
                user_id=user["_id"],
                file_info=file_info
            )

            # PDF 생성 및 저장
            pdf_result = None
            if len(image_paths) > 0:
                storage_service = StorageService(self.db)
                pdf_result = await storage_service.create_pdf_from_images(
                    user_id=user["_id"],
                    storage_id=storage_id,
                    image_paths=image_paths,
                    pdf_title=title,
                    primary_file_id=mp3_file_id  # MP3 파일과 연결
                )
                logger.info(f"PDF created successfully: {pdf_result}")

            return ImageDocument(
                title=title,
                file_id=str(mp3_file_id), # 실제 MP3 파일의 ID 반환
                processed_files=[ImageMetadata(
                    filename=f"combined_{file_id}",
                    content_type="audio/mp3",
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
            files: List[UploadFile],
            user_id: str
    ):
        storage_id = None
        group_id = str(uuid.uuid4())  # 파일 그룹 ID

        try:
            user = await self.db["users"].find_one({"email": user_id})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            storage_id = await self.update_storage_count(
                user_id=user["_id"],
                storage_name=storage_name,
                file_count=1
            )

            combined_contents = []
            s3_keys = []

            for idx, file in enumerate(files):
                ocr_result = await self.call_clova_receipt_ocr(file)
                combined_contents.append(ocr_result)

                file.file.seek(0)
                contents = await file.read()
                s3_key = f"receipts/{user_id}/{group_id}/receipt_{idx + 1}.{file.filename.split('.')[-1]}"
                s3_keys.append(s3_key)

                self.s3_client.put_object(
                    Bucket=S3_BUCKET_NAME,
                    Key=s3_key,
                    Body=contents,
                    ContentType=file.content_type
                )

            file_info = {
                "title": title,
                "filename": f"combined_{group_id}",
                "s3_key": s3_keys[0],
                "additional_s3_keys": s3_keys[1:],
                "contents": combined_contents,
                "file_size": sum(f.size for f in files),  # file.read() 대신 size 속성 사용
                "mime_type": "application/json",
                "is_primary": True
            }

            file_id = await self.save_file_metadata(
                storage_id=storage_id,
                user_id=user["_id"],
                file_info=file_info
            )

            return {
                "file_id": file_id,
                "ocr_results": combined_contents
            }

        except Exception as e:
            if storage_id:
                await self.storage_collection.update_one(
                    {"_id": ObjectId(storage_id)},
                    {"$inc": {"file_count": -1}}
                )
            raise e