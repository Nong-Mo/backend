import os

import requests
from asyncio import Timeout
from aiohttp import TooManyRedirects
from locust import HttpUser, task, between
import json
import random
from PIL import Image
import io
import time
import logging
from datetime import datetime
import uuid

from requests import RequestException

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MockResponse:
    """Mock HTTP 응답 클래스"""
    def __init__(self, content, status_code=200):
        self.status_code = status_code
        self._content = content
        self.content = content
        self._text = content.decode() if isinstance(content, bytes) else content

    def json(self):
        return json.loads(self._text)

    @property
    def text(self):
        return self._text


class MockClient:
    """Mock HTTP 클라이언트 클래스"""
    def __init__(self, base_url=""):
        self.base_url = base_url
        self.mock_responses = {
            "/images/upload": {
                "file_id": "mock_file_id",
                "message": "Files processed and stored successfully"
            },
            "/receipt/ocr": {
                "file_id": "mock_receipt_id",
                "ocr_results": [{
                    "storeInfo": {"name": "테스트 상점"},
                    "totalPrice": 35000,
                    "date": "2024-01-14"
                }]
            },
            "/llm/query": {
                "type": "chat",
                "message": "안녕하세요! 무엇을 도와드릴까요?",
                "data": None
            }
        }

    def get_mock_response(self, url, **kwargs):
        for endpoint, response_data in self.mock_responses.items():
            if url.endswith(endpoint):
                return MockResponse(json.dumps(response_data).encode())
        return None

    def post(self, url, **kwargs):
        mock_response = self.get_mock_response(url, **kwargs)
        if mock_response:
            return mock_response
        return MockResponse(json.dumps({"status": "success"}).encode())

    def get(self, url, **kwargs):
        mock_response = self.get_mock_response(url, **kwargs)
        if mock_response:
            return mock_response
        return MockResponse(json.dumps({"status": "success"}).encode())


class A2DUser(HttpUser):
    host = "http://localhost:8000"
    wait_time = between(1, 3)

    STORAGE_NAMES = {
        "book": "책",
        "receipt": "영수증",
        "goods": "굿즈",
        "film": "필름 사진",
        "document": "서류",
        "ticket": "티켓"
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.token = None
        self.user_email = None
        self.user_id = None
        self.mock_client = MockClient(self.host)

    def generate_unique_id(self):
        return f"{int(time.time())}_{uuid.uuid4().hex[:6]}"

    def on_start(self):
        try:
            self.user_id = self.generate_unique_id()
            self.user_email = "Test@example.com"
            self.token = self.get_token(self.user_email, "Test123!@#")  # 정상적으로 self와 인자를 넘김
            logger.info(f"Test user created: {self.user_email} with token: {self.token}")
        except Exception as e:
            logger.error(f"Startup error: {str(e)}")
            raise

    def get_token(self, user_email, password):
        url = "http://localhost:8000/auth/signin"
        headers = {"Content-Type": "application/json"}
        data = json.dumps({"email": user_email, "password": password})

        # POST 요청을 보냄
        response = requests.post(url, headers=headers, data=data)

        if response.status_code == 200:
            # 'access_token'을 반환하도록 수정
            return response.json()['data']['access_token']
        else:
            raise Exception(f"Failed to get token, status code: {response.status_code}, body: {response.text}")

    @task(1)
    def health_check(self):
        start_time = time.time()  # 시작 시간
        with self.client.get("/health", catch_response=True) as response:
            elapsed_time = time.time() - start_time  # 소요 시간 계산
            if response.status_code == 200:
                response.success()
                logger.info(f"Health check successful in {elapsed_time:.4f} seconds")
            else:
                response.failure(f"Health check failed in {elapsed_time:.4f} seconds: {response.status_code}")

    @task(2)
    def get_storage_list(self):
        if not self.token:
            logger.error("No token available")
            return

        start_time = time.time()
        headers = {"token": self.token}
        response = self.client.get("/storage/list", headers=headers)
        elapsed_time = time.time() - start_time

        if response.status_code == 200:
            logger.info(f"Storage list retrieved successfully in {elapsed_time:.4f} seconds")
        else:
            logger.error(f"Storage list failed in {elapsed_time:.4f} seconds: {response.text}")

    @task(2)
    def get_storage_detail(self):
        if not self.token:
            logger.error("No token available")
            return

        storage_type = random.choice(list(self.STORAGE_NAMES.keys()))
        headers = {"token": self.token}

        start_time = time.time()
        response = self.client.get(f"/storage/{storage_type}", headers=headers)
        elapsed_time = time.time() - start_time

        if response.status_code == 200:
            logger.info(f"Storage detail for '{storage_type}' retrieved in {elapsed_time:.4f} seconds")
        else:
            logger.error(f"Storage detail failed in {elapsed_time:.4f} seconds: {response.text}")

    @task(3)
    def upload_image(self):
        """이미지 업로드 테스트"""
        if not self.token:
            logger.error("No token available")
            return

        try:
            # 로컬에서 테스트 이미지 읽기
            #image_path = "../test_images/test_sample_01.jpeg"
            image_path = os.path.join(os.path.dirname(__file__), '../test_images/test_sample_01.jpeg')
            with open(image_path, "rb") as img_file:
                img_bytes = img_file.read()

            files = {
                'files': ('test_sample_01.jpeg', img_bytes, 'image/jpeg')
            }

            data = {
                'storage_name': random.choice(['책', '영수증', '굿즈', '필름 사진', '서류', '티켓']),
                'title': f'테스트_{datetime.now().strftime("%Y%m%d_%H%M%S")}',
                'pages_vertices_data': json.dumps([None]),  # 좌표 데이터를 선택적으로 추가
                'user_id': "Test@example.com",  # 실제 사용자 이메일로 대체
                'image_service': "ocr_service_instance"  # OCR 처리 서비스 인스턴스를 대체해야 할 부분
            }

            headers = {
                "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJUZXN0QGV4YW1wbGUuY29tIiwiZXhwIjoxNzM2ODc2Mjg0fQ.ovtkFP95nREdzjhRi0zAgLE0HeVuLizZlZOufbT6ayE", # Bearer 방식으로 토큰을 헤더에 첨부
            }

            try:
                response = self.mock_client.post(
                    "/images/upload",
                    headers=headers,
                    data=data,
                    files=files
                )

                if response.status_code == 200:
                    logger.info("Image uploaded successfully")
                elif response.status_code == 422:
                    logger.error(f"Validation Error: {response.json()}")
                else:
                    logger.error(f"Error {response.status_code}: {response.text}")

            except Timeout as e:
                logger.error(f"Request Timeout Error: {e}")
            except TooManyRedirects as e:
                logger.error(f"Too Many Redirects Error: {e}")
            except ConnectionError as e:
                logger.error(f"Connection Error: {e}")
            except RequestException as e:
                logger.error(f"Request failed due to a general error: {e}")

        except ValueError as e:
            logger.error(f"ValueError: {e} - 문제 있는 데이터 유형이거나 잘못된 형식")
        except KeyError as e:
            logger.error(f"KeyError: {e} - 누락된 필드 또는 잘못된 키")
        except Exception as e:
            logger.error(f"Unexpected error during image creation and preparation: {str(e)}")

    @task(3)
    def process_receipt_ocr(self):
        if not self.token:
            logger.error("No token available")
            return

        try:
            img = Image.new('RGB', (100, 100), color='white')
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='JPEG')
            img_bytes.seek(0)

            files = [('files', ('receipt.jpg', img_bytes, 'image/jpeg'))]
            data = {
                'storage_name': self.STORAGE_NAMES['receipt'],
                'title': f'영수증_{datetime.now().strftime("%Y%m%d_%H%M%S")}',
                'pages_vertices_data': json.dumps([None])
            }
            headers = {"token": self.token}

            start_time = time.time()
            response = self.mock_client.post("/receipt/ocr", headers=headers, data=data, files=files)
            elapsed_time = time.time() - start_time

            if response.status_code == 200:
                logger.info(f"Receipt OCR processed successfully in {elapsed_time:.4f} seconds")
            else:
                logger.error(f"Receipt OCR failed in {elapsed_time:.4f} seconds: {response.text}")

        except Exception as e:
            logger.error(f"Receipt OCR error: {str(e)}")

    @task(4)
    def llm_query(self):
        if not self.token:
            logger.error("No token available")
            return

        headers = {
            "token": self.token,
            "Content-Type": "application/json"
        }
        queries = ["오늘 저장한 영수증 분석해줘", "이 책의 내용을 요약해줘", "재미있는 이야기 들려줘", "최근 파일 찾아줘"]
        query_data = {"query": random.choice(queries)}

        start_time = time.time()
        response = self.mock_client.post("/llm/query", headers=headers, json=query_data)
        elapsed_time = time.time() - start_time

        if response.status_code == 200:
            logger.info(f"LLM query processed successfully in {elapsed_time:.4f} seconds")
        else:
            logger.error(f"LLM query failed in {elapsed_time:.4f} seconds: {response.text}")

    def on_stop(self):
        logger.info(f"Test completed for user {self.user_email}")
