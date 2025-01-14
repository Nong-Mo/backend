from locust import HttpUser, task, between
import json
import random
from PIL import Image
import io
import time
import logging
from datetime import datetime
import uuid

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mock 응답 데이터
MOCK_RESPONSES = {
    "image_upload": {
        "file_id": "mock_file_id",
        "message": "Files processed and stored successfully"
    },
    "receipt_ocr": {
        "file_id": "mock_receipt_id",
        "ocr_results": [{
            "storeInfo": {"name": "테스트 상점"},
            "totalPrice": 35000,
            "date": "2024-01-14"
        }]
    },
    "llm_query": {
        "type": "chat",
        "message": "안녕하세요! 무엇을 도와드릴까요?",
        "data": None
    }
}


class A2DUser(HttpUser):
    """A2D 서비스 성능 테스트를 위한 사용자 클래스"""

    host = "http://localhost:8000"
    wait_time = between(1, 3)

    # 한글 보관함 이름 사용
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

    def generate_unique_id(self):
        """고유한 ID 생성"""
        return f"{int(time.time())}_{uuid.uuid4().hex[:6]}"

    def on_start(self):
        """테스트 시작 시 사용자 생성 및 로그인"""
        try:
            self.user_id = self.generate_unique_id()
            self.user_email = f"test_user_{self.user_id}@test.com"

            # 회원가입
            signup_data = {
                "email": self.user_email,
                "password": "Test1234!@#",
                "password_confirmation": "Test1234!@#",
                "nickname": f"테스트{self.user_id[-4:]}"
            }

            with self.client.post(
                    "/auth/signup",
                    json=signup_data,
                    catch_response=True
            ) as response:
                if response.status_code == 201:
                    logger.info(f"Signup successful for {self.user_email}")
                    response.success()
                else:
                    logger.error(f"Signup failed: {response.text}")
                    response.failure(f"Signup failed: {response.text}")
                    return

            # 로그인
            login_data = {
                "email": self.user_email,
                "password": signup_data["password"]
            }

            with self.client.post(
                    "/auth/signin",
                    json=login_data,
                    catch_response=True
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    self.token = data["data"]["access_token"]
                    logger.info(f"Login successful for {self.user_email}")
                    response.success()
                else:
                    logger.error(f"Login failed: {response.text}")
                    response.failure(f"Login failed: {response.text}")

        except Exception as e:
            logger.error(f"Startup error: {str(e)}")
            raise

    @task(1)
    def health_check(self):
        """서버 상태 확인"""
        with self.client.get(
                "/health",
                catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Health check failed: {response.status_code}")

    @task(2)
    def get_storage_list(self):
        """보관함 목록 조회"""
        if not self.token:
            logger.error("No token available")
            return

        headers = {"token": self.token}
        with self.client.get(
                "/storage/list",
                headers=headers,
                catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                logger.error(f"Storage list failed: {response.text}")
                response.failure(f"Storage list failed: {response.text}")

    @task(2)
    def get_storage_detail(self):
        """특정 보관함 상세 정보 조회"""
        if not self.token:
            logger.error("No token available")
            return

        storage_type = random.choice(list(self.STORAGE_NAMES.keys()))
        headers = {"token": self.token}

        with self.client.get(
                f"/storage/{storage_type}",
                headers=headers,
                catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                logger.error(f"Storage detail failed: {response.text}")
                response.failure(f"Storage detail failed: {response.text}")

    @task(3)
    def upload_image(self):
        """이미지 업로드 테스트"""
        if not self.token:
            logger.error("No token available")
            return

        try:
            # 간단한 테스트 이미지 생성
            img = Image.new('RGB', (100, 100), color='white')
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='JPEG')
            img_bytes.seek(0)

            files = [
                ('files', ('test.jpg', img_bytes, 'image/jpeg'))
            ]

            data = {
                'storage_name': random.choice(list(self.STORAGE_NAMES.values())),
                'title': f'테스트_{datetime.now().strftime("%Y%m%d_%H%M%S")}',
                'pages_vertices_data': json.dumps([None])
            }

            headers = {"token": self.token}

            with self.client.post(
                    "/images/upload",
                    headers=headers,
                    data=data,
                    files=files,
                    catch_response=True
            ) as response:
                if response.status_code in [200, 201]:
                    response.success()
                    # Mock 응답으로 교체
                    response._content = json.dumps(MOCK_RESPONSES["image_upload"]).encode()
                else:
                    logger.error(f"Image upload failed: {response.text}")
                    response.failure(f"Image upload failed: {response.text}")

        except Exception as e:
            logger.error(f"Image upload error: {str(e)}")

    @task(3)
    def process_receipt_ocr(self):
        """영수증 OCR 처리 테스트"""
        if not self.token:
            logger.error("No token available")
            return

        try:
            img = Image.new('RGB', (100, 100), color='white')
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='JPEG')
            img_bytes.seek(0)

            files = [
                ('files', ('receipt.jpg', img_bytes, 'image/jpeg'))
            ]

            data = {
                'storage_name': '영수증',
                'title': f'영수증_{datetime.now().strftime("%Y%m%d_%H%M%S")}',
                'pages_vertices_data': json.dumps([None])
            }

            headers = {"token": self.token}

            with self.client.post(
                    "/receipt/ocr",
                    headers=headers,
                    data=data,
                    files=files,
                    catch_response=True
            ) as response:
                if response.status_code in [200, 201]:
                    response.success()
                    # Mock 응답으로 교체
                    response._content = json.dumps(MOCK_RESPONSES["receipt_ocr"]).encode()
                else:
                    logger.error(f"Receipt OCR failed: {response.text}")
                    response.failure(f"Receipt OCR failed: {response.text}")

        except Exception as e:
            logger.error(f"Receipt OCR error: {str(e)}")

    @task(4)
    def llm_query(self):
        """LLM 쿼리 테스트"""
        if not self.token:
            logger.error("No token available")
            return

        headers = {
            "token": self.token,
            "Content-Type": "application/json"
        }

        queries = [
            "오늘 저장한 영수증 분석해줘",
            "이 책의 내용을 요약해줘",
            "재미있는 이야기 들려줘",
            "최근 파일 찾아줘"
        ]

        query_data = {
            "query": random.choice(queries)
        }

        with self.client.post(
                "/llm/query",
                headers=headers,
                json=query_data,
                catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
                # Mock 응답으로 교체
                response._content = json.dumps(MOCK_RESPONSES["llm_query"]).encode()
            else:
                logger.error(f"LLM query failed: {response.text}")
                response.failure(f"LLM query failed: {response.text}")

    def on_stop(self):
        """테스트 종료 시 정리 작업"""
        logger.info(f"Test completed for user {self.user_email}")