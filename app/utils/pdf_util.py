import logging
from app.core.config import (
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    S3_BUCKET_NAME,
    S3_REGION_NAME
)
from botocore.config import Config
# Configure logger
logger = logging.getLogger(__name__)

import os
import uuid
import tempfile
import datetime
import img2pdf
import boto3
import logging
from bson import ObjectId
from typing import List, Optional, Dict
from fastapi import HTTPException
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import matplotlib.pyplot as plt
import matplotlib
from io import BytesIO
import json

from app.core.exceptions import PDFGenerationError, StorageError

# Configure logger
#logger = logging.getLogger(__name__)

# matplotlib에서 한글 폰트 설정
import matplotlib.font_manager as fm


def setup_matplotlib_font():
    """Matplotlib 한글 폰트 설정"""
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        font_path = os.path.join(project_root, 'app', 'static', 'fonts', 'NanumGothicLight.ttf')

        if not os.path.exists(font_path):
            raise PDFGenerationError(f"폰트 파일을 찾을 수 없습니다: {font_path}")

        # 폰트 추가
        font_prop = fm.FontProperties(fname=font_path)
        matplotlib.rcParams['font.family'] = font_prop.get_name()

        # 폰트 매니저에 폰트 추가
        fm.fontManager.addfont(font_path)

        # 캐시 재생성
        fm.findfont(font_prop)

    except Exception as e:
        logger.error(f"Matplotlib 폰트 설정 실패: {str(e)}")
        raise PDFGenerationError(f"Matplotlib 폰트 설정 실패: {str(e)}")


# Matplotlib 폰트 설정 실행
setup_matplotlib_font()

class PDFUtil:
    def __init__(self, db):
        self.db = db
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=S3_REGION_NAME,
            config=Config(signature_version='s3v4')
        )
        # 한글 폰트 등록
        self.font_name = 'NanumGothicLight'  # 폰트 이름 저장
        # PDF와 Matplotlib 둘 다를 위한 폰트 경로 설정
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        self.font_path = os.path.join(project_root, 'app', 'static', 'fonts', 'NanumGothicLight.ttf')

        if not os.path.exists(self.font_path):
            raise PDFGenerationError(f"폰트 파일을 찾을 수 없습니다: {self.font_path}")
        self._register_korean_font()

        # PDF와 Matplotlib 둘 다를 위한 폰트 경로 설정
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        self.font_path = os.path.join(project_root, 'app', 'static', 'fonts', 'NanumGothicLight.ttf')

        if not os.path.exists(self.font_path):
            raise PDFGenerationError(f"폰트 파일을 찾을 수 없습니다: {self.font_path}")

    def _register_korean_font(self):
        """ReportLab을 위한 한글 폰트 등록"""
        try:
            pdfmetrics.registerFont(TTFont(self.font_name, self.font_path))
            logger.info(f"Successfully registered {self.font_name} font from {self.font_path}")
        except Exception as e:
            logger.error(f"Could not register Korean font for ReportLab: {str(e)}")
            raise PDFGenerationError(f"ReportLab 폰트 등록 실패: {str(e)}")

    async def create_text_pdf(self, user_id: ObjectId, storage_id: ObjectId, content: str, title: str) -> Dict[str, any]:
        """텍스트 내용을 PDF로 변환"""
        try:
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
                doc = SimpleDocTemplate(
                    tmp_file.name,
                    pagesize=letter,
                    rightMargin=72,
                    leftMargin=72,
                    topMargin=72,
                    bottomMargin=72
                )

                styles = getSampleStyleSheet()
                title_style = ParagraphStyle(
                    'CustomTitle',
                    parent=styles['Title'],
                    fontName=self.font_name,
                    fontSize=24,
                    spaceAfter=30
                )
                content_style = ParagraphStyle(
                    'CustomBody',
                    parent=styles['Normal'],
                    fontName=self.font_name,
                    fontSize=12,
                    leading=14
                )

                story = []
                story.append(Paragraph(title, title_style))
                story.append(Spacer(1, 12))
                story.append(Paragraph(content, content_style))

                doc.build(story)

                # UUID를 문자열로 생성
                pdf_id = str(uuid.uuid4())
                s3_key = f"pdfs/{user_id}/{pdf_id}.pdf"

                with open(tmp_file.name, 'rb') as pdf_file:
                    self.s3_client.upload_fileobj(
                        pdf_file,
                        S3_BUCKET_NAME,
                        s3_key,
                        ExtraArgs={'ContentType': 'application/pdf'}
                    )

                file_size = os.path.getsize(tmp_file.name)

                # MongoDB에 저장할 때 PDF ID를 문자열로 반환
                return {
                    "file_id": pdf_id,  # UUID 문자열
                    "s3_key": s3_key,
                    "file_size": file_size
                }

        except Exception as e:
            logger.error(f"PDF 생성 실패: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"PDF 생성 실패: {str(e)}"
            )

    async def create_analysis_pdf(
            self,
            user_id: ObjectId,
            storage_id: ObjectId,
            content: str,
            structured_data: dict,
            title: str
    ) -> Dict[str, any]:
        """분석 내용과 그래프를 포함한 PDF 생성"""
        try:
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
                # PDF 페이지 크기와 여백 설정
                doc = SimpleDocTemplate(
                    tmp_file.name,
                    pagesize=letter,
                    rightMargin=36,  # 여백 축소
                    leftMargin=36,
                    topMargin=36,
                    bottomMargin=36
                )

                # 스타일 설정
                styles = getSampleStyleSheet()
                title_style = ParagraphStyle(
                    'CustomTitle',
                    parent=styles['Title'],
                    fontName=self.font_name,
                    fontSize=20,  # 폰트 크기 축소
                    spaceAfter=20
                )
                heading_style = ParagraphStyle(
                    'Heading',
                    parent=styles['Heading1'],
                    fontName=self.font_name,
                    fontSize=16,  # 폰트 크기 축소
                    spaceAfter=15
                )
                content_style = ParagraphStyle(
                    'CustomBody',
                    parent=styles['Normal'],
                    fontName=self.font_name,
                    fontSize=10,  # 폰트 크기 축소
                    leading=12
                )

                story = []
                story.append(Paragraph(title, title_style))
                story.append(Spacer(1, 10))

                # 메타데이터 추가
                if structured_data.get("metadata"):
                    story.append(Paragraph("영수증 정보", heading_style))
                    for key, value in structured_data["metadata"].items():
                        story.append(Paragraph(f"{key}: {value}", content_style))
                    story.append(Spacer(1, 10))

                # 금액 정보 추가
                if structured_data.get("amounts"):
                    story.append(Paragraph("금액 분석", heading_style))
                    for key, value in structured_data["amounts"].items():
                        story.append(Paragraph(
                            f"{key}: {value:,}원",
                            content_style
                        ))
                    story.append(Spacer(1, 10))

                    # 금액 그래프 추가 (크기 조정됨)
                    if len(structured_data["amounts"]) > 0:
                        graph_image = self._create_graph(structured_data["amounts"])
                        if graph_image:
                            story.append(Paragraph("금액 분석 그래프", heading_style))
                            img = Image(BytesIO(graph_image))
                            # 이미지 크기를 PDF 페이지에 맞게 조정
                            img.drawWidth = 400
                            img.drawHeight = 300
                            story.append(img)
                            story.append(Spacer(1, 10))

                # 전체 분석 내용 추가
                story.append(Paragraph("상세 분석", heading_style))
                story.append(Paragraph(content, content_style))

                # PDF 생성
                doc.build(story)

                # S3에 업로드
                pdf_id = str(uuid.uuid4())
                s3_key = f"analysis/{user_id}/{pdf_id}.pdf"

                with open(tmp_file.name, 'rb') as pdf_file:
                    self.s3_client.upload_fileobj(
                        pdf_file,
                        S3_BUCKET_NAME,
                        s3_key,
                        ExtraArgs={'ContentType': 'application/pdf'}
                    )

                file_size = os.path.getsize(tmp_file.name)

                return {
                    "file_id": pdf_id,
                    "s3_key": s3_key,
                    "file_size": file_size
                }

        except Exception as e:
            logger.error(f"분석 PDF 생성 실패: {str(e)}")
            if isinstance(e, (PDFGenerationError, StorageError)):
                raise e
            raise PDFGenerationError(f"PDF 생성 실패: {str(e)}")

    def _extract_numbers(self, content: str) -> Dict[str, int]:
        """영수증 텍스트에서 숫자 데이터 추출"""
        try:
            # JSON 형식으로 된 분석 결과가 있는지 확인
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    # OCR 결과에서 금액 정보 추출
                    receipt_amounts = {}

                    # 총액 정보 추출
                    if 'totalPrice' in data:
                        receipt_amounts['총액'] = int(data['totalPrice'])

                    # 결제 금액 추출
                    if 'payment' in data and 'amount' in data['payment']:
                        receipt_amounts['결제금액'] = int(data['payment']['amount'])

                    # 부가세 추출
                    if 'tax' in data:
                        receipt_amounts['부가세'] = int(data['tax'])

                    return receipt_amounts
            except json.JSONDecodeError:
                pass

            # 텍스트에서 금액 패턴 추출
            import re
            numbers = {}

            # 금액 패턴 (예: "총액: 50,000원" 또는 "50,000원")
            amount_pattern = r'([가-힣\s]+)?[\s:]*([\d,]+)원'
            matches = re.findall(amount_pattern, content)

            for label, amount in matches:
                key = label.strip() if label.strip() else "금액"
                value = int(amount.replace(',', ''))
                numbers[key] = value

            return numbers

        except Exception as e:
            logger.error(f"숫자 데이터 추출 실패: {str(e)}")
            return {}

    def _create_graph(self, data: Dict[str, int]) -> Optional[bytes]:
        """데이터를 바탕으로 그래프 생성"""
        try:
            if not data:
                return None

            # 그래프 크기를 PDF 페이지 크기에 맞게 조정
            plt.figure(figsize=(6, 4))  # 크기를 더 작게 조정
            plt.clf()

            # 막대 그래프 생성
            x = range(len(data))
            plt.bar(x, list(data.values()), color='skyblue')
            plt.xticks(x, list(data.keys()), rotation=45, ha='right')

            # 그래프 스타일 설정
            plt.title('금액 분석', fontsize=12, pad=15)
            plt.grid(True, axis='y', linestyle='--', alpha=0.7)

            # 여백 조정
            plt.tight_layout()

            # 값 라벨 추가 (폰트 크기 축소)
            for i, v in enumerate(data.values()):
                plt.text(i, v, f'{v:,}원', ha='center', va='bottom', fontsize=8)

            # 이미지로 저장
            img_data = BytesIO()
            plt.savefig(img_data, format='png', dpi=150, bbox_inches='tight')
            img_data.seek(0)
            plt.close()

            return img_data.getvalue()

        except Exception as e:
            logger.error(f"그래프 생성 실패: {str(e)}")
            return None

    async def create_pdf_from_images(
            self,
            user_id: ObjectId,
            storage_id: str,
            image_paths: List[str],
            pdf_title: str,
            primary_file_id: Optional[str] = None,
            storage_type: str = "pdfs"
    ) -> Dict[str, str]:
        """
        이미지들을 PDF로 변환하고 S3에 저장합니다.
        """
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                pdf_path = os.path.join(temp_dir, "combined.pdf")
                with open(pdf_path, "wb") as f:
                    f.write(img2pdf.convert(image_paths))

                pdf_id = str(uuid.uuid4())
                s3_key = f"{storage_type}/{user_id}/{pdf_id}.pdf"

                with open(pdf_path, "rb") as f:
                    self.s3_client.upload_fileobj(
                        f,
                        S3_BUCKET_NAME,
                        s3_key,
                        ExtraArgs={'ContentType': 'application/pdf'}
                    )

                now = datetime.datetime.now(datetime.UTC)
                pdf_doc = {
                    "storage_id": ObjectId(storage_id),
                    "user_id": user_id,
                    "title": pdf_title,
                    "s3_key": s3_key,
                    "created_at": now,
                    "updated_at": now,
                    "mime_type": "application/pdf",
                    "file_size": os.path.getsize(pdf_path)
                }

                if primary_file_id:
                    pdf_doc.update({
                        "primary_file_id": ObjectId(primary_file_id),
                        "is_primary": False
                    })
                else:
                    pdf_doc.update({
                        "is_primary": True
                    })

                result = await self.db.files.insert_one(pdf_doc)
                return {
                    "file_id": str(result.inserted_id),
                    "s3_key": s3_key
                }

        except Exception as e:
            logger.error(f"PDF 생성 실패: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"PDF 생성 실패: {str(e)}"
            )