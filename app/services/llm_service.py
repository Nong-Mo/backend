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
from app.models.llm import FileSearchResult

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

    async def search_file(self, user_id: str, query: str) -> FileSearchResult:
        try:
            user = await self.users_collection.find_one({"email": user_id})
            if not user:
                return {
                    "type": "error",
                    "message": "사용자를 찾을 수 없습니다.",
                    "data": None
                }

            file = await self.files_collection.find_one({
                "user_id": user["_id"],
                "title": {"$regex": query, "$options": "i"}
            })

            if file:
                return {
                    "type": "file_found",
                    "message": f"'{file['title']}' 파일을 찾았습니다. 클릭하시면 해당 파일로 이동합니다.",
                    "data": {
                        "file_id": str(file["_id"]),
                        "storage_id": str(file["storage_id"]),
                        "title": file["title"]
                    }
                }

            return {
                "type": "chat",
                "message": f"'{query}'와 일치하는 파일을 찾을 수 없습니다.",
                "data": None
            }

        except Exception as e:
            logger.error(f"Error searching file for user {user_id}: {str(e)}")
            return {
                "type": "error",
                "message": "파일 검색 중 오류가 발생했습니다.",
                "data": None
            }

    async def process_query(self, user_id: str, query: str, new_chat: bool = False):
        """
        사용자의 질문을 처리하고 AI 응답을 생성합니다.

        Args:
            user_id (str): 사용자 이메일 주소
            query (str): 사용자의 질문
            new_chat (bool, optional): 새로운 채팅 세션 시작 여부

        Returns:
            FileSearchResult: 검색 결과 또는 채팅 응답

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

            # 파일 검색 의도 파악
            search_prompt = f"""
            사용자의 질문이 파일 검색 요청인지 파악해주세요.
            질문: {query}

            규칙:
            1. "~~ 파일 찾아줘", "~~ 문서 어디있어?" 등의 패턴 인식
            2. 검색할 파일명만 추출
            3. 검색 의도가 있으면 "SEARCH:파일명" 형식으로 반환
            4. 검색 의도가 없으면 "CHAT" 반환
            """

            intention = chat.send_message(search_prompt)

            if intention.text.startswith("SEARCH:"):
                search_query = intention.text.split("SEARCH:")[1].strip()
                result = await self.search_file(user_id, search_query)

                # 채팅 기록 저장
                await self.save_chat_message(user_id, "user", query)
                await self.save_chat_message(user_id, "model", result["message"])

                return result

            # 일반 대화 처리
            files = await self.get_user_files(user_id)
            logger.debug(f"Found {len(files)} files for user")

            # 파일이 없는 경우 처리
            if not files:
                response = chat.send_message("파일을 찾을 수 없습니다.")
                await self.save_chat_message(user_id, "user", query)
                await self.save_chat_message(user_id, "model", response.text)
                return {
                    "type": "chat",
                    "message": response.text,
                    "data": None
                }

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
                return {
                    "type": "error",
                    "message": response.text,
                    "data": None
                }

            # AI 프롬프트 구성
            prompt = f"""
                        안녕하세요! 저는 AI 어시스턴스, 당신의 똑똑하고 믿음직한 디지털 비서이자 크리에이티브 파트너입니다. 바쁜 당신의 일상과 창작 활동을 효율적으로 지원하기 위해 언제나 준비되어 있어요. 무엇이든 편하게 말씀해주세요!

                        당신의 파일 정보는 다음과 같아요:
                        {json.dumps(context, ensure_ascii=False, indent=2)}

                        무엇을 도와드릴까요? (질문): {query}

                        제가 답변드릴 때 지킬 규칙은 다음과 같아요:

                        1. 파일/보관함 이동 안내:
                            - 특정 파일이나 보관함을 빠르게 찾으실 수 있도록 정확하게 안내해 드릴게요.
                            - 안내 형식: "[파일명/보관함명] (으)로 이동하시려면, 하단의 '[파일명/보관함명]' 버튼을 클릭하시면 됩니다."
                            - 예시: "이번 달 영수증들을 확인하시려면, 하단의 '이번 달 영수증' 버튼을 클릭하시면 됩니다." 또는 "프로젝트 기획서 파일을 찾으시려면, 상단의 '프로젝트 기획서' 버튼을 클릭하시면 됩니다."

                        2. 금액 정보 안내 (영수증 분석):
                            - 영수증 파일에서 금액 정보를 확인하실 때는, 총액과 결제 수단은 기본, 필요시 항목별 지출 내역까지 분석해 드릴게요.
                            - 안내 형식: "총 [금액]원이 [결제 수단]으로 결제되었으며, [주요 항목]에 [금액]원을 지출하셨네요."
                            - 예시: "총 35,000원이 카카오뱅크 체크카드로 결제되었으며, 식료품에 20,000원, 교통비에 15,000원을 지출하셨네요."

                        3. 파일 목록 안내:
                            - 파일 종류별 개수를 먼저 알려드린 후, 최신순으로 핵심 정보만 정리해 드릴게요.
                            - 정리 방식:
                                - 영수증: "[가게 이름] - [금액] - [날짜]"
                                - 책: "[제목] - [저자] - [출판일]"
                                - 티켓: "[행사 이름] - [날짜] - [장소]"
                                - 기타 파일: "[제목] - [날짜]"
                            - 예시:
                                "최근 영수증 3건은 다음과 같습니다.
                                - 스타벅스 - 5,600원 - 2024년 10월 26일
                                - 교보문고 - 18,000원 - 2024년 10월 25일
                                - 올리브영 - 23,500원 - 2024년 10월 24일"

                        4. 내용 요약/분석:
                            - 긴 텍스트 파일의 내용을 빠르게 파악하실 수 있도록 핵심 내용을 요약하거나, 필요한 분석을 제공해 드릴게요.
                            - 책의 경우: "주요 등장인물, 줄거리, 주제 등을 요약해 드립니다. 원하시면 뒷이야기를 창작해 드릴 수도 있어요."
                            - 영수증의 경우: "월별/주별 지출 분석, 항목별 지출 비율 등을 시각화하여 제공해 드릴 수 있습니다."

                        5. 창작 지원 (책 뒷이야기, 삽화) 및 PDF/오디오북 동시 제작:
                            - 책의 뒷이야기를 원하시면, 원작의 분위기와 설정을 고려하여 흥미로운 이야기를 창작해 드릴게요.
                            - 삽화를 원하시면, 텍스트 내용을 바탕으로 어울리는 이미지를 생성하거나, 기존 이미지에서 영감을 얻어 새로운 이미지를 제안해 드릴 수 있습니다.
                            - PDF는 텍스트와 그림을 보기 좋게 구성하여 제공하며, 오디오북은 다양한 TTS 옵션으로 제작됩니다.
                            - 생성된 PDF와 오디오북은 파일 목록에서 확인하고 선택하여 감상하실 수 있습니다.

                        6. 영수증 스캔 모드 특별 기능:
                            - 영수증 활용 내역을 분석하여 당신의 소비 패턴을 꼼꼼하게 지적해 드릴게요.
                            - 당신의 소비 습관을 명확하게 이해하실 수 있도록 구체적인 예시와 함께 설명해 드릴 거예요.
                            - 더 효율적인 소비 생활을 위한 다양한 팁과 제안도 함께 제공해 드릴게요.
                            - 영수증 내역을 보기 쉽도록 다양한 그래프로 시각화하여 제공해 드릴게요.
                            - 월별/주별 총 지출액 비교, 항목별 지출 비율, 시간 흐름에 따른 지출 변화 추이 등을 그래프로 보여드릴 수 있습니다.

                        7. 항상 기억할 사항:
                            - 답변은 최대한 간결하고 명확하게 드릴게요.
                            - 중복된 정보는 피하고, 필요한 정보만 전달해 드릴게요.
                            - 개발자이자 크리에이터인 당신의 니즈를 이해하고, 효율성과 창의성을 동시에 높일 수 있도록 최선을 다할게요.
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
            return {
                "type": "chat",
                "message": response.text,
                "data": None
            }

        except Exception as e:
            logger.error(f"Error in process_query: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"LLM 처리 중 오류가 발생했습니다: {str(e)}"
            )