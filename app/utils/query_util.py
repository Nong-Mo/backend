# app/utils/query_util.py
import json
import logging
import datetime
from fastapi import HTTPException
from typing import Dict, Any, List
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

    async def save_chat_message(self, user_id: str, role: str, content: str | dict):
        """
        채팅 메시지를 저장합니다. OCR 결과의 경우 구조화된 형태로 저장합니다.
        """
        message_doc = {
            "user_id": user_id,
            "role": role,
            "content": content,
            "timestamp": datetime.datetime.now()
        }

        # OCR 결과인 경우 메시지 타입 지정
        if isinstance(content, dict) and "type" not in message_doc:
            message_doc["type"] = "ocr_result"

        await self.chat_collection.insert_one(message_doc)

    async def get_chat_history(self, user_id: str, limit: int = 20) -> List[Dict]:
        """채팅 기록을 조회하고 OCR 결과를 포함하여 반환합니다."""
        history = await self.chat_collection.find(
            {"user_id": user_id}
        ).sort("timestamp", -1).limit(limit).to_list(length=None)

        # OCR 결과는 별도로 처리
        formatted_history = []
        for msg in reversed(history):
            if msg.get("type") == "ocr_result":
                formatted_history.append({
                    "role": msg["role"],
                    "parts": json.dumps(msg["content"], ensure_ascii=False),
                    "type": "ocr_result"
                })
            else:
                formatted_history.append({
                    "role": msg["role"],
                    "parts": msg["content"]
                })

        return formatted_history

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

    async def process_query(self, user_id: str, query: str, new_chat: bool = False, save_to_history: bool = True) -> Dict[str, Any]:
        try:
            chat_history = await self.get_chat_history(user_id)

            # OCR 결과가 있는지 확인
            ocr_data = None
            for msg in reversed(chat_history):
                if isinstance(msg.get("content"), dict) and msg.get("type") == "ocr_result":
                    ocr_data = msg["content"]
                    break

            if new_chat or user_id not in self.chat_sessions:
                self.chat_sessions[user_id] = self.model.start_chat(
                    history=[] if new_chat else chat_history
                )

            chat = self.chat_sessions[user_id]

            # 파일 목록 가져오기
            files = await self.get_user_files(user_id)

            # OCR 데이터가 있는 경우 프롬프트에 포함
            ocr_context = ""
            if ocr_data:
                ocr_context = f"\n\nOCR 분석 결과:\n{json.dumps(ocr_data, ensure_ascii=False, indent=2)}"

            prompt = f"""
            [시스템 메시지]
            당신은 사용자의 파일을 관리하고 분석하는 AI 어시스턴트입니다. 

            [컨텍스트]
            - 사용자가 보유한 파일 수: {len(files)}개
            - 사용자의 파일 제목 목록: {', '.join(f['title'] for f in files)}
            {ocr_context}

            [사용자 질문]
            {query}

            [응답 규칙]
            1. 사용자가 특정 파일을 찾고 있다면, 해당 파일을 찾아서 알려주세요.
            2. OCR 결과가 있다면, 구체적인 금액과 정보를 포함하여 분석해주세요.
            3. 일반적인 질문이라면, 파일 내용을 참조하여 자연스럽게 답변해주세요.
            4. 모든 답변은 한국어로, 친절하고 자연스럽게 해주세요.
            """

            response = chat.send_message(prompt)
            response_text = response.text.strip()

            if save_to_history:
                await self.save_chat_message(user_id, "user", query)
                await self.save_chat_message(user_id, "model", response_text)

            return {
                "type": "chat",
                "message": response_text,
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