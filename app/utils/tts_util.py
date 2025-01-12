# app/utils/tts_util.py
import asyncio
import ssl
import urllib.parse
import urllib.request
import logging
from fastapi import HTTPException
import boto3
from io import BytesIO
from pydub import AudioSegment
from typing import List
import math
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

    def _split_text(self, text: str, max_length: int = 1900) -> List[str]:
        """
        텍스트를 지정된 최대 길이로 분할합니다.
        마지막 문장이 잘리지 않도록 마침표를 기준으로 분할합니다.
        
        Args:
            text (str): 분할할 텍스트
            max_length (int): 각 부분의 최대 길이
            
        Returns:
            List[str]: 분할된 텍스트 리스트
        """
        if len(text) <= max_length:
            return [text]

        parts = []
        while text:
            if len(text) <= max_length:
                parts.append(text)
                break

            # max_length 위치부터 역순으로 가장 가까운 마침표 찾기
            split_pos = max_length
            while split_pos > max_length // 2:
                if text[split_pos] in '.!?':
                    split_pos += 1  # 마침표 다음 위치로 이동
                    break
                split_pos -= 1
            
            # 적절한 분할 위치를 찾지 못한 경우
            if split_pos <= max_length // 2:
                split_pos = max_length

            parts.append(text[:split_pos])
            text = text[split_pos:].strip()

        return parts

    async def _get_audio_from_api(self, text: str) -> bytes:
        """
        네이버 클로바 TTS API를 호출하여 오디오 바이너리를 받아옵니다.
        
        Args:
            text (str): 변환할 텍스트
            
        Returns:
            bytes: 오디오 바이너리 데이터
        """
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
        
        return response

    async def convert_text_to_speech(self, text: str, filename: str, title: str) -> str:
        """
        텍스트를 음성으로 변환하고 S3에 저장합니다.
        긴 텍스트의 경우 자동으로 분할하여 처리합니다.

        Args:
            text (str): 변환할 텍스트
            filename (str): 저장할 파일 이름
            title (str): 파일 제목

        Returns:
            str: S3에 저장된 파일의 키
        """
        try:
            # 텍스트 분할
            text_parts = self._split_text(text)
            audio_segments = []

            # 각 부분에 대해 TTS 변환 수행
            for part in text_parts:
                audio_binary = await self._get_audio_from_api(part)
                audio = AudioSegment.from_mp3(BytesIO(audio_binary))
                audio_segments.append(audio)

            # 모든 오디오 세그먼트 병합
            combined_audio = audio_segments[0]
            for segment in audio_segments[1:]:
                combined_audio += segment

            # 병합된 오디오를 바이트로 변환
            output = BytesIO()
            combined_audio.export(output, format='mp3')
            final_audio = output.getvalue()

            # S3에 업로드
            s3_key = f"tts/{filename}/{title}.mp3"
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.s3_client.put_object(
                    Bucket=S3_BUCKET_NAME,
                    Key=s3_key,
                    Body=final_audio,
                    ContentType='audio/mp3'
                )
            )

            return s3_key

        except Exception as e:
            logger.error(f"TTS Error: {str(e)}")
            raise HTTPException(status_code=500, detail=f"TTS 생성 실패: {str(e)}")