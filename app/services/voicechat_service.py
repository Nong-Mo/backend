import boto3
import os
import uuid
import json
import google.generativeai as genai
from datetime import datetime
from typing import Optional, Dict, List
from fastapi import UploadFile, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import asyncio
import logging
from app.core.config import (
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    AWS_VOICE_CHAT_ACCESS_KEY,
    AWS_VOICE_CHAT_SECRET_ACCESS_KEY,
    S3_REGION_NAME,
    S3_BUCKET_NAME,
    GOOGLE_API_KEY
)
import aiohttp

logger = logging.getLogger(__name__)

class VoiceChatService:
    def __init__(self, db: AsyncIOMotorClient):
        """
        VoiceChatService 초기화.

        Args:
            db (AsyncIOMotorClient): MongoDB 비동기 클라이언트 인스턴스.
        """
        self.db = db
        self.chat_collection = db.voicechat
        self.chat_sessions = {}  # 메모리 내 채팅 세션 캐시
        
        # AWS 클라이언트 초기화
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=S3_REGION_NAME
        )
        self.transcribe_client = boto3.client(
            'transcribe',
            aws_access_key_id=AWS_VOICE_CHAT_ACCESS_KEY,
            aws_secret_access_key=AWS_VOICE_CHAT_SECRET_ACCESS_KEY,
            region_name=S3_REGION_NAME
        )
        self.polly_client = boto3.client(
            'polly',
            aws_access_key_id=AWS_VOICE_CHAT_ACCESS_KEY,
            aws_secret_access_key=AWS_VOICE_CHAT_SECRET_ACCESS_KEY,
            region_name=S3_REGION_NAME
        )
        
        # Gemini 초기화
        genai.configure(api_key=GOOGLE_API_KEY)
        self.model = genai.GenerativeModel("gemini-2.0-flash-exp")

    async def process_voice_chat(
        self,
        user_id: str,
        audio_file: UploadFile,
        session_id: Optional[str] = None
    ) -> Dict:
        """
        음성 파일을 처리하여 AI와 대화하고 응답을 반환합니다.

        Args:
            user_id (str): 사용자 ID.
            audio_file (UploadFile): 업로드된 음성 파일.
            session_id (Optional[str]): 대화 세션 ID. 없으면 새로 생성.

        Returns:
            Dict: AI의 응답 메시지와 음성 콘텐츠 URL을 포함하는 딕셔너리.
        """
        try:
            # 세션 관리
            if not session_id:
                session_id = str(uuid.uuid4())
                await self._create_chat_session(user_id, session_id)
            
            # 1. 음성 파일을 S3에 업로드 (비동기)
            audio_s3_key = await self._upload_audio_to_s3(audio_file)
            
            # 2. STT 처리 시작 (비동기)
            transcribe_task = asyncio.create_task(self._transcribe_audio(audio_s3_key))
            
            # 3. 이전 대화 내용 로드 (비동기)
            history_task = asyncio.create_task(self._load_chat_history(session_id))
            
            # 두 작업 동시 실행
            text, history = await asyncio.gather(transcribe_task, history_task)
            
            # 4. Gemini로 대화 처리
            response = await self._process_gemini_chat(text, history)
            
            # 5. TTS 변환 (비동기)
            audio_response = await self._synthesize_speech(response)
            
            # 6. 대화 내용 저장 (비동기)
            save_task = asyncio.create_task(self._save_chat_messages(
                session_id=session_id,
                user_message=text,
                assistant_message=response
            ))
            
            # 응답 반환 전에 저장 완료 대기
            await save_task
            
            return {
                "message": response,
                "audio_content": audio_response,
                "session_id": session_id
            }

        except Exception as e:
            logger.error(f"Error in process_voice_chat: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    async def _upload_audio_to_s3(self, audio_file: UploadFile) -> str:
        """
        음성 파일을 S3에 업로드합니다.

        Args:
            audio_file (UploadFile): 업로드할 음성 파일.

        Returns:
            str: S3에 저장된 음성 파일의 키.
        """
        try:
            file_id = str(uuid.uuid4())
            s3_key = f"voicechat/audio/{file_id}.wav"
            
            content = await audio_file.read()
            content_type = audio_file.content_type or "audio/wav"
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.s3_client.put_object(
                    Bucket=S3_BUCKET_NAME,
                    Key=s3_key,
                    Body=content,
                    ContentType=content_type
                )
            )
            return s3_key
        except Exception as e:
            logger.error(f"Error uploading to S3: {str(e)}")
            raise

    async def _transcribe_audio(self, audio_s3_key: str) -> str:
        """
        Amazon Transcribe로 음성을 텍스트로 변환합니다.

        Args:
            audio_s3_key (str): S3에 저장된 음성 파일의 키.

        Returns:
            str: 변환된 텍스트.
        """
        try:
            job_name = f"transcribe-{str(uuid.uuid4())}"
            
            # Transcribe 작업 시작
            
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.transcribe_client.start_transcription_job(
                    TranscriptionJobName=job_name,
                    Media={'MediaFileUri': f"s3://{S3_BUCKET_NAME}/{audio_s3_key}"},
                    MediaFormat='wav',
                    LanguageCode='ko-KR'
                )
            )

            # 작업 완료 대기
            while True:
                status = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.transcribe_client.get_transcription_job(
                        TranscriptionJobName=job_name
                    )
                )
                if status['TranscriptionJob']['TranscriptionJobStatus'] in ['COMPLETED', 'FAILED']:
                    break
                await asyncio.sleep(0.5)

            if status['TranscriptionJob']['TranscriptionJobStatus'] == 'COMPLETED':
                # 변환된 텍스트를 가져오기
                transcript_uri = status['TranscriptionJob']['Transcript']['TranscriptFileUri']
                # URI에서 텍스트를 가져오는 로직 추가
                # 예를 들어, HTTP GET 요청을 통해 텍스트를 가져올 수 있습니다.
                return await self._fetch_transcript(transcript_uri)
            else:
                # 실패 이유 로깅
                failure_reason = status['TranscriptionJob'].get('FailureReason', 'Unknown error')
                logger.error(f"Transcription job failed: {failure_reason}")
                raise Exception(f"Transcription failed: {failure_reason}")
        except Exception as e:
            logger.error(f"Error in transcription: {str(e)}")
            raise

    async def _fetch_transcript(self, uri: str) -> str:
        """주어진 URI에서 변환된 텍스트를 가져옵니다."""
        async with aiohttp.ClientSession() as session:
            async with session.get(uri) as response:
                if response.status == 200:
                    # 텍스트로 읽어서 JSON 파싱
                    transcript_text = await response.text()
                    transcript_data = json.loads(transcript_text)
                    return transcript_data['results']['transcripts'][0]['transcript']
                else:
                    raise Exception("Failed to fetch transcript")

    async def _process_gemini_chat(self, text: str, history: List[Dict]) -> str:
        """
        Gemini로 대화 처리합니다.

        Args:
            text (str): 사용자의 입력 텍스트.
            history (List[Dict]): 이전 대화 기록.

        Returns:
            str: AI의 응답 텍스트.
        """
        try:
            # 대화 기록을 Gemini 형식으로 변환
            chat = self.model.start_chat(history=[
                {"role": msg["role"], "parts": [msg["content"]]}
                for msg in history
            ])
            
            # 새 메시지 전송 및 응답 생성
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: chat.send_message(text)
            )
            
            return response.text
        except Exception as e:
            logger.error(f"Error in Gemini chat: {str(e)}")
            raise

    async def _synthesize_speech(self, text: str) -> bytes:
        """
        Amazon Polly로 텍스트를 음성으로 변환합니다.

        Args:
            text (str): 변환할 텍스트.

        Returns:
            bytes: 변환된 음성 데이터.
        """
        try:
            # 텍스트를 3000자 이하로 분할
            max_length = 3000
            audio_streams = []

            for i in range(0, len(text), max_length):
                chunk = text[i:i + max_length]
                response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.polly_client.synthesize_speech(
                        Text=chunk,
                        OutputFormat='mp3',
                        VoiceId='Seoyeon',
                        LanguageCode='ko-KR'
                    )
                )
                audio_streams.append(response['AudioStream'].read())

            # 모든 음성 스트림을 결합
            combined_audio = b''.join(audio_streams)
            return combined_audio

        except Exception as e:
            logger.error(f"Error in speech synthesis: {str(e)}")
            raise

    async def _create_chat_session(self, user_id: str, session_id: str):
        """
        새로운 채팅 세션을 생성합니다.

        Args:
            user_id (str): 사용자 ID.
            session_id (str): 생성할 세션 ID.
        """
        now = datetime.utcnow()
        await self.chat_collection.insert_one({
            "session_id": session_id,
            "user_id": user_id,
            "messages": [],
            "created_at": now,
            "updated_at": now
        })

    async def _load_chat_history(self, session_id: str) -> List[Dict]:
        """
        세션의 대화 기록을 로드합니다.

        Args:
            session_id (str): 대화 세션 ID.

        Returns:
            List[Dict]: 대화 메시지 목록.
        """
        session = await self.chat_collection.find_one({"session_id": session_id})
        return session.get("messages", []) if session else []

    async def _save_chat_messages(self, session_id: str, user_message: str, assistant_message: str):
        """
        대화 내용을 데이터베이스에 저장합니다.

        Args:
            session_id (str): 대화 세션 ID.
            user_message (str): 사용자의 메시지.
            assistant_message (str): AI의 응답 메시지.
        """
        now = datetime.utcnow()
        messages = [
            {"role": "user", "content": user_message, "timestamp": now},
            {"role": "assistant", "content": assistant_message, "timestamp": now}
        ]
        
        await self.chat_collection.update_one(
            {"session_id": session_id},
            {
                "$push": {"messages": {"$each": messages}},
                "$set": {"updated_at": now}
            }
        )
