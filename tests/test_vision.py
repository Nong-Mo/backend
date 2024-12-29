# 필요한 라이브러리:
# pip install pytest
# pip install google-cloud-vision
# pip install python-dotenv

import pytest
from pathlib import Path
from google.cloud import vision
import os
from dotenv import load_dotenv

# 테스트 이미지 디렉토리 설정
TEST_IMAGE_DIR = Path(__file__).parent / "test_images"
TEST_IMAGE_DIR.mkdir(exist_ok=True)

@pytest.fixture(scope="module")
def vision_client():
    """Vision API 클라이언트 픽스처"""
    load_dotenv()
    return vision.ImageAnnotatorClient()

def test_text_detection(vision_client):
    """텍스트 감지 테스트"""
    # 테스트 이미지 경로
    image_path = TEST_IMAGE_DIR / "sample.jpg"
    
    # 이미지 파일 존재 확인
    assert image_path.exists(), f"테스트 이미지가 없습니다: {image_path}"
    
    # 이미지 로드
    with open(image_path, "rb") as image_file:
        content = image_file.read()
    
    image = vision.Image(content=content)
    response = vision_client.text_detection(image=image)
    
    # API 응답 검증
    assert not response.error.message, f"API 에러: {response.error.message}"
    assert len(response.text_annotations) > 0, "텍스트가 감지되지 않았습니다"
    
    # 감지된 텍스트 출력
    detected_text = response.text_annotations[0].description
    print(f"\n감지된 텍스트:\n{detected_text}")

def test_vision_client_initialization(vision_client):
    """Vision 클라이언트 초기화 테스트"""
    assert vision_client is not None, "Vision 클라이언트 초기화 실패" 