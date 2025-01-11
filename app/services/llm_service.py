"""
LLM (Large Language Model) 서비스 클래스
이 클래스는 Google의 Gemini API를 사용하여 대화형 AI 서비스를 제공합니다.
MongoDB를 사용하여 사용자 정보, 파일, 채팅 기록을 관리합니다.
"""
import asyncio
import uuid

import boto3
import google.generativeai as genai
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
import json
from app.core.config import GOOGLE_API_KEY, S3_REGION_NAME, AWS_SECRET_ACCESS_KEY, AWS_ACCESS_KEY_ID, S3_BUCKET_NAME
import logging
import datetime
from typing import Dict

# 로깅 설정
logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self, mongodb_client: AsyncIOMotorClient):
        """
        LLMService 클래스의 생성자

        Args:
            mongodb_client (AsyncIOMotorClient): MongoDB 비동기 클라이언트 인스턴스

        초기화 항목:
            - MongoDB 컬렉션 (files, users, chat_history)
            - 사용자별 채팅 세션 저장소
            - Gemini API 설정
        """
        # S3 클라이언트 초기화 추가
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=S3_REGION_NAME
        )
        self.db = mongodb_client
        self.files_collection = self.db.files  # 파일 정보 저장 컬렉션
        self.users_collection = self.db.users  # 사용자 정보 저장 컬렉션
        self.chat_collection = self.db.chat_history  # 채팅 기록 저장 컬렉션
        self.storage_collection = self.db.storages  # 추가

        # 사용자별 채팅 세션을 메모리에 저장하는 딕셔너리
        # Key: 사용자 ID, Value: Gemini 채팅 세션 객체
        self.chat_sessions: Dict[str, genai.ChatSession] = {}

        # Gemini API 초기화 및 모델 설정
        genai.configure(api_key=GOOGLE_API_KEY)
        self.model = genai.GenerativeModel("gemini-2.0-flash-exp")

    async def verify_user_access(self, user_id: str):
        """
        사용자의 접근 권한을 확인합니다.

        Args:
            user_id (str): 사용자 이메일 주소

        Returns:
            dict: 사용자 정보를 담은 딕셔너리

        Raises:
            HTTPException: 사용자를 찾을 수 없는 경우 404 에러
        """
        user = await self.users_collection.find_one({"email": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    async def get_user_files(self, user_id: str):
        """
        사용자의 파일 목록을 조회합니다.

        Args:
            user_id (str): 사용자 이메일 주소

        Returns:
            list: 사용자의 파일 목록
        """
        user = await self.verify_user_access(user_id)
        files = await self.files_collection.find({
            "user_id": user["_id"]
        }).to_list(length=None)
        return files

    async def get_chat_history(self, user_id: str, limit: int = 20):
        """
        사용자의 최근 채팅 기록을 가져옵니다.

        Args:
            user_id (str): 사용자 이메일 주소
            limit (int, optional): 가져올 최대 메시지 수. 기본값 20

        Returns:
            list: 채팅 기록 리스트. 각 항목은 {"role": str, "parts": str} 형식
        """
        history = await self.chat_collection.find(
            {"user_id": user_id}
        ).sort("timestamp", -1).limit(limit).to_list(length=None)

        formatted_history = [
            {"role": msg["role"], "parts": msg["content"]}
            for msg in reversed(history)  # 시간순으로 정렬
        ]

        logger.info(f"Retrieved {len(formatted_history)} chat history messages for user {user_id}")
        logger.debug(f"Chat history: {json.dumps(formatted_history, ensure_ascii=False)}")

        return formatted_history

    async def save_chat_message(self, user_id: str, role: str, content: str):
        """
        채팅 메시지를 데이터베이스에 저장합니다.

        Args:
            user_id (str): 사용자 이메일 주소
            role (str): 메시지 작성자 역할 ("user" 또는 "model")
            content (str): 메시지 내용
        """
        await self.chat_collection.insert_one({
            "user_id": user_id,
            "role": role,
            "content": content,
            "timestamp": datetime.datetime.now()
        })

    def get_or_create_chat_session(self, user_id: str, history: list = None, new_chat: bool = False):
        """
        사용자의 채팅 세션을 가져오거나 새로 생성합니다.

        Args:
            user_id (str): 사용자 이메일 주소
            history (list, optional): 이전 채팅 기록
            new_chat (bool, optional): 새로운 채팅 세션 시작 여부

        Returns:
            genai.ChatSession: Gemini 채팅 세션 객체
        """
        if new_chat or user_id not in self.chat_sessions:
            self.chat_sessions[user_id] = self.model.start_chat(history=history or [])
        return self.chat_sessions[user_id]

    async def start_new_chat(self, user_id: str):
        """
        새로운 채팅 세션을 시작하고 이전 채팅 기록을 삭제합니다.

        Args:
            user_id (str): 사용자 이메일 주소

        Returns:
            dict: 성공 메시지를 담은 딕셔너리
        """
        try:
            # 메모리에서 채팅 세션 삭제
            if user_id in self.chat_sessions:
                del self.chat_sessions[user_id]

            # DB에서 채팅 기록 삭제
            delete_result = await self.chat_collection.delete_many({"user_id": user_id})

            logger.info(f"Started new chat session for user: {user_id}")
            logger.info(f"Deleted {delete_result.deleted_count} chat messages from history")

            return {
                "status": "success",
                "message": "새로운 채팅이 시작되었습니다.",
                "deleted_messages": delete_result.deleted_count
            }

        except Exception as e:
            logger.error(f"Error in start_new_chat: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"새 채팅 시작 중 오류가 발생했습니다: {str(e)}"
            )

    async def save_story(
            self,
            user_email: str,  # 파라미터명을 명확하게 변경
            storage_name: str,
            title: str
    ):
        try:
            # 사용자 ObjectId 조회
            user = await self.users_collection.find_one({"email": user_email})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            user_id = user["_id"]  # MongoDB용 ObjectId

            # storage 조회
            storage = await self.storage_collection.find_one({
                "user_id": user_id,  # ObjectId 사용
                "name": storage_name
            })

            if not storage:
                raise HTTPException(
                    status_code=404,
                    detail=f"Storage '{storage_name}' not found for this user"
                )

            # 채팅 기록은 이메일로 조회
            last_message = await self.chat_collection.find_one(
                {"user_id": user_email},  # 채팅 기록은 이메일로 저장됨
                sort=[("timestamp", -1)]
            )

            if not last_message:
                raise HTTPException(
                    status_code=404,
                    detail="저장할 단편소설을 찾을 수 없습니다."
                )

            content = last_message["content"]
            file_id = str(uuid.uuid4())
            filename = f"{title}.txt"
            s3_key = f"stories/{user_email}/{file_id}/{filename}"  # S3는 이메일 사용

            # S3 업로드
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.s3_client.put_object(
                    Bucket=S3_BUCKET_NAME,
                    Key=s3_key,
                    Body=content.encode('utf-8'),
                    ContentType='text/plain'
                ),
                *()
            )

            # MongoDB에는 ObjectId로 저장
            file_doc = {
                "storage_id": storage["_id"],
                "user_id": user_id,  # ObjectId 사용
                "title": title,
                "filename": filename,
                "s3_key": s3_key,
                "contents": content,
                "file_size": len(content.encode('utf-8')),
                "mime_type": "text/plain",
                "created_at": datetime.datetime.now(),
                "updated_at": datetime.datetime.now(),
                "is_primary": True
            }

            result = await self.files_collection.insert_one(file_doc)
            return str(result.inserted_id)

        except Exception as e:
            logger.error(f"Error saving story for user {user_email}: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"단편소설 저장 중 오류가 발생했습니다: {str(e)}"
            )

    async def process_query(self, user_id: str, query: str, new_chat: bool = False):
        """
        사용자의 질문을 처리하고 AI 응답을 생성합니다.

        Args:
            user_id (str): 사용자 이메일 주소
            query (str): 사용자의 질문
            new_chat (bool, optional): 새로운 채팅 세션 시작 여부

        Returns:
            str: AI의 응답 텍스트

        Raises:
            HTTPException: AI 응답 생성 실패 시 500 에러
        """
        try:
            logger.info(f"Processing query: {query} for user: {user_id}")

            # 채팅 히스토리 로드
            chat_history = await self.get_chat_history(user_id)

            # 채팅 세션 가져오기 또는 생성
            chat = self.get_or_create_chat_session(
                user_id,
                chat_history if not new_chat else None,
                new_chat
            )

            # 사용자 파일 목록 조회
            files = await self.get_user_files(user_id)
            logger.debug(f"Found {len(files)} files for user")

            # 파일이 없는 경우 처리
            if not files:
                response = chat.send_message("파일을 찾을 수 없습니다.")
                await self.save_chat_message(user_id, "user", query)
                await self.save_chat_message(user_id, "model", response.text)
                return response.text

            # 파일 정보를 컨텍스트로 변환
            context = []
            for file in files:
                try:
                    file_info = {
                        "title": file.get("title", "제목 없음"),
                        "created_at": file["created_at"].isoformat(),
                        "type": file.get("mime_type", "unknown"),
                        "content": file.get("contents", {}),
                    }
                    context.append(file_info)
                except KeyError as e:
                    logger.error(f"Missing required field in file {file.get('title', 'unknown')}: {e}")
                    continue
                except Exception as e:
                    logger.error(f"Error processing file {file.get('title', 'unknown')}: {e}")
                    continue

            # 컨텍스트 변환 실패 처리
            if not context:
                response = chat.send_message("파일 처리 중 오류가 발생했습니다.")
                await self.save_chat_message(user_id, "user", query)
                await self.save_chat_message(user_id, "model", response.text)
                return response.text

            # AI 프롬프트 구성
            # 파일 정보와 사용자 질문을 포함하여 상세한 응답 지침 제공
            prompt = f"""
            다음은 사용자의 파일 정보입니다:
            {json.dumps(context, ensure_ascii=False, indent=2)}

            사용자 질문: {query}

            [상세한 응답 규칙...]  # 실제 프롬프트에는 모든 규칙이 포함됨
            """

            # Gemini API로 응답 생성
            logger.debug("Sending prompt to Gemini API")
            response = chat.send_message(prompt)

            # 응답 생성 실패 처리
            if not response or not response.text:
                raise HTTPException(
                    status_code=500,
                    detail="LLM이 응답을 생성하지 못했습니다."
                )

            # 대화 내용 저장
            await self.save_chat_message(user_id, "user", query)
            await self.save_chat_message(user_id, "model", response.text)

            logger.info("Successfully generated response from Gemini")
            return response.text

        except Exception as e:
            # 오류 로깅 및 예외 처리
            logger.error(f"Error in process_query: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"LLM 처리 중 오류가 발생했습니다: {str(e)}"
            )