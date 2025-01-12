# app/utils/query_util.py
import json
import logging
import datetime
from fastapi import HTTPException
from typing import Dict, Any, Optional
from app.models.llm import FileSearchResult
import google.generativeai as genai
from app.core.config import GOOGLE_API_KEY


logger = logging.getLogger(__name__)


class QueryProcessor:
    def __init__(self, db, chat_collection):
        self.db = db
        self.chat_collection = chat_collection
        genai.configure(api_key=GOOGLE_API_KEY)
        self.model = genai.GenerativeModel("gemini-2.0-flash-exp")
        self.chat_sessions = {}

    async def save_chat_message(self, user_id: str, role: str, content: str):
        """채팅 메시지를 저장합니다."""
        await self.chat_collection.insert_one({
            "user_id": user_id,
            "role": role,
            "content": content,
            "timestamp": datetime.datetime.now()
        })

    async def get_chat_history(self, user_id: str, limit: int = 20):
        """채팅 기록을 조회합니다."""
        history = await self.chat_collection.find(
            {"user_id": user_id}
        ).sort("timestamp", -1).limit(limit).to_list(length=None)

        return [
            {"role": msg["role"], "parts": msg["content"]}
            for msg in reversed(history)
        ]

    async def search_file(self, user_id: str, query: str) -> FileSearchResult:
        """파일을 검색합니다."""
        try:
            user = await self.db.users.find_one({"email": user_id})
            if not user:
                return {
                    "type": "error",
                    "message": "사용자를 찾을 수 없습니다.",
                    "data": None
                }

            file = await self.db.files.find_one({
                "user_id": user["_id"],
                "title": {"$regex": query, "$options": "i"}
            })

            if file:
                return {
                    "type": "file_found",
                    "message": f"'{file['title']}' 파일을 찾았습니다.",
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
            logger.error(f"검색 오류: {str(e)}")
            return {
                "type": "error",
                "message": "파일 검색 중 오류가 발생했습니다.",
                "data": None
            }

    # app/utils/query_util.py

    async def process_query(self, user_id: str, query: str, new_chat: bool = False, save_to_history: bool = True) -> \
    Dict[str, Any]:
        """사용자 질의를 처리합니다."""
        try:
            chat_history = await self.get_chat_history(user_id)

            if new_chat or user_id not in self.chat_sessions:
                self.chat_sessions[user_id] = self.model.start_chat(
                    history=[] if new_chat else chat_history
                )

            chat = self.chat_sessions[user_id]

            # 검색 의도 파악
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
                if save_to_history:
                    await self.save_chat_message(user_id, "user", query)
                    await self.save_chat_message(user_id, "model", result["message"])
                return result

            # 일반 대화 처리
            files = await self.get_user_files(user_id)

            if not files:
                response = chat.send_message("파일을 찾을 수 없습니다.")
                if save_to_history:
                    await self.save_chat_message(user_id, "user", query)
                    await self.save_chat_message(user_id, "model", response.text)
                return {
                    "type": "chat",
                    "message": response.text,
                    "data": None
                }

            # LLM 처리 시 원본 contents는 변경하지 않도록 수정
            context = [
                {
                    "title": file.get("title", "제목 없음"),
                    "created_at": file["created_at"].isoformat(),
                    "type": file.get("mime_type", "unknown"),
                    "content": file.get("contents", "")  # 원본 내용 그대로 유지
                }
                for file in files
            ]

            prompt = f"""
            [시스템 메시지]
            당신은 사용자의 파일을 관리하고 분석하는 AI 어시스턴트입니다.

            [사용자 파일 정보]
            {json.dumps(context, ensure_ascii=False, indent=2)}

            [사용자 질문]
            {query}
            """

            response = chat.send_message(prompt)

            if save_to_history:
                await self.save_chat_message(user_id, "user", query)
                await self.save_chat_message(user_id, "model", response.text)

            return {
                "type": "chat",
                "message": response.text,  # LLM 응답은 message에만 저장
                "data": None
            }

        except Exception as e:
            logger.error(f"쿼리 처리 오류: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"쿼리 처리 중 오류가 발생했습니다: {str(e)}"
            )

    async def get_user_files(self, user_id: str):
        """사용자의 파일 목록을 조회합니다."""
        user = await self.db.users.find_one({"email": user_id})
        if not user:
            return []
        return await self.db.files.find({
            "user_id": user["_id"]
        }).to_list(length=None)