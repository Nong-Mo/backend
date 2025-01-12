# app/utils/tts_util.py
import asyncio
import ssl
import urllib.parse
import urllib.request
import logging
from fastapi import HTTPException
import boto3
from app.core.config import (
    NCP_CLIENT_ID,
    NCP_CLIENT_SECRET,
    NCP_TTS_API_URL,
    S3_BUCKET_NAME,
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    S3_REGION_NAME
)

logger = logging.getLogger(__name__)

class TTSUtil:
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=S3_REGION_NAME
        )

    async def convert_text_to_speech(self, text: str, filename: str, title: str) -> str:
        """
        텍스트를 음성으로 변환하고 S3에 저장합니다.

        Args:
            text (str): 변환할 텍스트
            filename (str): 저장할 파일 이름
            title (str): 파일 제목

        Returns:
            str: S3에 저장된 파일의 키
        """
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
                lambda: urllib.request.urlopen(request, context=ssl_context).read(),
                *()
            )

            s3_key = f"tts/{filename}/{title}.mp3"

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

            return s3_key

        except Exception as e:
            logger.error(f"TTS Error: {str(e)}")
            raise HTTPException(status_code=500, detail=f"TTS 생성 실패: {str(e)}")