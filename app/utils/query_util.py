# app/utils/query_util.py

import json
import logging
import datetime
from fastapi import HTTPException
from typing import Dict, Any, List
from app.models.llm import FileSearchResult
import google.generativeai as genai
from app.core.config import GOOGLE_API_KEY
from app.models.message_types import MessageType

logger = logging.getLogger(__name__)


class QueryProcessor:
    def __init__(self, db, chat_collection):
        self.db = db
        self.chat_collection = chat_collection
        self.files_collection = self.db.files
        self.users_collection = self.db.users
        genai.configure(api_key=GOOGLE_API_KEY)
        self.model = genai.GenerativeModel("gemini-2.0-flash-exp")
        self.chat_sessions = {}

    async def search_file(self, user_id: str, query: str) -> FileSearchResult:
        """파일을 검색합니다."""
        try:
            user = await self.users_collection.find_one({"email": user_id})
            if not user:
                return {
                    "type": "error",
                    "message": "사용자를 찾을 수 없습니다.",
                    "data": None
                }

            # 파일 제목과 내용에서 검색
            search_query = {
                "user_id": user["_id"],
                "$or": [
                    {"title": {"$regex": query, "$options": "i"}},
                    {"contents": {"$regex": query, "$options": "i"}}
                ]
            }

            files = await self.files_collection.find(search_query).to_list(length=None)

            if files:
                # 여러 파일이 발견된 경우
                if len(files) > 1:
                    file_list = [f"- {file['title']}" for file in files]
                    return {
                        "type": "file_found",
                        "message": f"관련된 파일들을 찾았습니다:\n" + "\n".join(file_list),
                        "data": {
                            "files": [{
                                "file_id": str(file["_id"]),
                                "storage_id": str(file["storage_id"]),
                                "title": file["title"]
                            } for file in files]
                        }
                    }
                
                # 단일 파일인 경우
                file = files[0]
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
                "message": f"'{query}'와 관련된 파일을 찾을 수 없습니다.",
                "data": None
            }

        except Exception as e:
            logger.error(f"검색 오류: {str(e)}")
            return {
                "type": "error",
                "message": "파일 검색 중 오류가 발생했습니다.",
                "data": None
            }

    async def save_chat_message(self, user_id: str, role: str, content: str | dict,
                                message_type: MessageType = MessageType.GENERAL,
                                data: Dict = None):
        """
        채팅 메시지를 저장합니다.
        - message_type: 메시지 종류 구분
        - data: 추가 메타데이터 (뒷이야기의 경우 original_title, is_sequel 등)
        """
        message_doc = {
            "user_id": user_id,
            "role": role,
            "content": content,
            "message_type": message_type.value,
            "timestamp": datetime.datetime.now()
        }

        if data:
            message_doc["data"] = data

        if isinstance(content, dict) and "type" not in message_doc:
            message_doc["type"] = "ocr_result"

        await self.chat_collection.insert_one(message_doc)

    async def get_chat_history(self, user_id: str, limit: int = 20) -> List[Dict]:
        """채팅 기록을 조회하고 OCR 결과를 포함하여 반환합니다."""
        history = await self.chat_collection.find(
            {"user_id": user_id}
        ).sort("timestamp", -1).limit(limit).to_list(length=None)

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

    async def get_user_files(self, user_id: str):
        """사용자의 파일 목록을 조회합니다."""
        user = await self.users_collection.find_one({"email": user_id})
        if not user:
            return []
        return await self.files_collection.find({"user_id": user["_id"]}).to_list(length=None)

    async def process_query(self, user_id: str, query: str, new_chat: bool = False, save_to_history: bool = True):
        """사용자 질의를 처리하고 적절한 응답을 생성합니다."""
        try:
            # ---------------------------------------------------------------
            # (A) 로컬 규칙 기반:
            #     사용자의 query 안에 "저장"/"save"가 포함되어 있다면
            #     LLM 의도 파악 없이 곧바로 저장 분기로 이동.
            # ---------------------------------------------------------------
            lower_query = query.lower()
            if ("저장" in lower_query) or ("save" in lower_query):
                logger.info("[Local Rule] '저장' or 'save' detected in user query. Triggering SAVE logic.")
                # 가장 최근 모델 메시지를 조회
                last_message = await self.chat_collection.find_one(
                    {"user_id": user_id, "role": "model"},
                    sort=[("timestamp", -1)]
                )
                if not last_message:
                    return {
                        "type": "error",
                        "message": "저장할 내용이 없습니다.",
                        "data": None
                    }
                return {
                    "type": "story_save_ready",
                    "message": "방금 작성한 이야기를 저장하시겠습니까?",
                    "data": {
                        "message_id": str(last_message["_id"]),
                        "content": last_message["content"],
                        "timestamp": last_message["timestamp"],
                        "original_title": last_message.get("data", {}).get("original_title"),
                        "is_sequel": last_message.get("data", {}).get("is_sequel", False),
                    },
                }

            # 채팅 히스토리 조회
            chat_history = await self.get_chat_history(user_id)
            
            if new_chat or user_id not in self.chat_sessions:
                self.chat_sessions[user_id] = self.model.start_chat(
                    history=[] if new_chat else chat_history
                )
            
            chat = self.chat_sessions[user_id]

            # 1단계: 사용자 의도 파악 (LLM 프롬프트)
            intention_prompt = f"""
            사용자의 의도를 파악해주세요.
            사용자 메시지: {query}

            다음과 같은 경우를 구분하여 판단하세요:
            1. 파일 검색 의도:
            - "~~ 찾아줘", "~~ 어디있어?"와 같은 직접적인 요청
            - "지난번에 저장한 ~~" 처럼 이전 파일을 찾는 경우
            - "~~ 관련 파일" 처럼 특정 주제의 파일을 찾는 경우

            2. 뒷이야기 요청 의도:
            - "뒷이야기 써줘", "다음 이야기 들려줘"
            - "이어서 써줘", "다음 내용 알려줘"
            - "후속 이야기 만들어줘"

            3. 저장 요청 의도:
            - "이걸 저장해줘", "저장해줘", "저장" 등의 직접적인 저장 요청
            - "이 이야기를 저장해줘" 등의 현재 컨텍스트 저장 요청
            - "이 내용 저장해줄래?" 등의 간접적인 저장 요청

            응답 형식은 반드시 아래 중 하나로만:
            - 파일 검색이면: SEARCH:검색할_키워드
            - 뒷이야기 요청이면: SEQUEL:파일제목
            - 저장 요청이면: SAVE
            - 일반 대화면: CHAT
            """

            # 의도 파악하기
            intention_response = chat.send_message(intention_prompt)
            logger.debug(f"[Intention Response] {intention_response.text}")

            # 2단계: 의도별 처리
            # (2-1) 파일 검색
            if intention_response.text.startswith("SEARCH:"):
                search_keyword = intention_response.text.split("SEARCH:", 1)[1].strip()
                search_result = await self.search_file(user_id, search_keyword)

                # 히스토리 저장 (response.text --> 여기서는 search_result["message"] 사용)
                if save_to_history:
                    await self.save_chat_message(user_id, "user", query)
                    await self.save_chat_message(
                        user_id, 
                        "model",
                        search_result["message"],  
                        MessageType.GENERAL
                    )
                
                return search_result
            
            # (2-2) 뒷이야기 요청
            elif intention_response.text.startswith("SEQUEL:"):
                title = intention_response.text.split("SEQUEL:", 1)[1].strip()
                
                # 해당 파일 검색
                user = await self.users_collection.find_one({"email": user_id})
                if not user:
                    return {
                        "type": "error",
                        "message": "사용자 정보를 찾을 수 없습니다.",
                        "data": None
                    }
                
                file = await self.files_collection.find_one({
                    "user_id": user["_id"],
                    "title": title,
                    "mime_type": {"$in": ["text/plain", "application/pdf", "audio/mp3"]}
                })
                
                if not file:
                    return {
                        "type": "error",
                        "message": "해당 이야기를 찾을 수 없습니다.",
                        "data": None
                    }
                
                # 스토리 컨텍스트 구성
                story_content = file['contents'] if isinstance(file['contents'], str) else file['contents'].get('text', '')
                story_context = f"""
                [원본 이야기 제목]
                {file['title']} 이 제목은 사용자가 임의로 등록한 책의 제목입니다. 등장인물의 이름이 아닙니다.
                
                [원본 이야기 내용]
                {story_content}
                """
                
                # 뒷이야기 생성을 위한 프롬프트
                sequel_prompt = f"""
                {story_context}
                
                [시스템 역할]
                당신은 숙련된 스토리텔러입니다. 당신의 임무는 본래의 이야기를 바탕으로 이야기를 창작하는 것입니다.
                
                [뒷이야기 작성 규칙]
                1. 원본 스토리의 흐름을 이어갈 것.
                2. 이야기 속에는 명시적으로 이름이 언급된 인물만 등장시켜 주세요.
                3. 새로운 인물을 추가해야 하는 경우, 원본 이야기의 맥락과 일치하는 인물만 추가해주세요.
                4. 원본의 세계관, 캐릭터, 설정을 유지할 것.
                5. 새로운 사건이나 전개를 추가할 것.
                6. 원본의 문체와 톤을 유지할 것.
                7. 기존 이야기의 복선이나 미해결된 부분을 활용할 것.
                8. 기존 이야기의 흐름을 최우선으로 반영하여 긍정적인 이야기로 무조건 마무리하지 않을 것.
                
                [응답 형식]
                - 바로 뒷이야기 본문으로 시작
                - 설명이나 메타 정보 없이 순수 이야기 내용만 작성
                - 부가적인 설명이나 맺음말 없이 이야기로만 구성

                [추가 지침]
                이야기는 500자 정도로 작성해주세요.
                
                [사용자 요청]
                {query}
                """
                
                response = chat.send_message(sequel_prompt)
                
                if save_to_history:
                    await self.save_chat_message(user_id, "user", query)
                    await self.save_chat_message(
                        user_id, 
                        "model", 
                        response.text, 
                        MessageType.BOOK_STORY
                    )

                return {
                    "type": "chat",
                    "message": response.text,
                    "data": {
                        "original_title": file['title'],
                        "is_sequel": True
                    }
                }
            
            # (2-3) 저장 요청 처리: LLM이 정확히 "SAVE"라고만 주는 케이스
            #       -> 하지만 "네, 저장하겠습니다" 등 다른 문장이면 안 잡히므로
            #          아래 '부분 일치' 처리를 추가
            elif intention_response.text == "SAVE":
                logger.info("[LLM Intention] Exactly 'SAVE' detected.")
                last_message = await self.chat_collection.find_one(
                    {"user_id": user_id, "role": "model"},
                    sort=[("timestamp", -1)]
                )
                if not last_message:
                    return {
                        "type": "error",
                        "message": "저장할 내용이 없습니다.",
                        "data": None
                    }

                return {
                    "type": "story_save_ready",
                    "message": "방금 작성한 이야기를 저장하시겠습니까?",
                    "data": {
                        "message_id": str(last_message["_id"]),
                        "content": last_message["content"],
                        "timestamp": last_message["timestamp"],
                        "original_title": last_message.get("data", {}).get("original_title"),
                        "is_sequel": last_message.get("data", {}).get("is_sequel", False),
                    },
                }

            # (2-4) 일반 대화 or '부분 일치' 후처리
            #       "SAVE"라는 단어가 아닌 "네, 저장하겠습니다" 등
            #       (저장/Save 키워드 포함 시 저장 분기로)
            normalized_intent = intention_response.text.lower()
            if ("저장" in normalized_intent) or ("save" in normalized_intent):
                logger.info("[LLM Partial Parse] '저장'/'save' found in LLM response. Triggering SAVE logic.")
                last_message = await self.chat_collection.find_one(
                    {"user_id": user_id, "role": "model"},
                    sort=[("timestamp", -1)]
                )
                if not last_message:
                    return {
                        "type": "error",
                        "message": "저장할 내용이 없습니다.",
                        "data": None
                    }

                return {
                    "type": "story_save_ready",
                    "message": "방금 작성한 이야기를 저장하시겠습니까?",
                    "data": {
                        "message_id": str(last_message["_id"]),
                        "content": last_message["content"],
                        "timestamp": last_message["timestamp"],
                        "original_title": last_message.get("data", {}).get("original_title"),
                        "is_sequel": last_message.get("data", {}).get("is_sequel", False),
                    },
                }

            # ----------------------------------------------------------------
            # (3) 일반 대화 처리
            # ----------------------------------------------------------------
            ocr_data = None
            for msg in reversed(chat_history):
                if isinstance(msg.get("content"), dict) and msg.get("type") == "ocr_result":
                    ocr_data = msg["content"]
                    await self.save_chat_message(
                        user_id,
                        "user",
                        ocr_data,
                        MessageType.RECEIPT_RAW
                    )
                    break

            files = await self.get_user_files(user_id)

            # OCR 데이터가 있는 경우 컨텍스트에 포함
            ocr_context = ""
            if ocr_data:
                ocr_context = f"\n\nOCR 분석 결과:\n{json.dumps(ocr_data, ensure_ascii=False, indent=2)}"

            # 대화 맥락 요약
            context_summary = "\n현재 대화 맥락:"
            current_context = set()
            for msg in reversed(chat_history):
                if msg.get("type") == "ocr_result" and "OCR" not in current_context:
                    context_summary += "\n- OCR 분석 진행중"
                    current_context.add("OCR")
                elif msg.get("message_type") == MessageType.BOOK_STORY.value and "STORY" not in current_context:
                    context_summary += "\n- 스토리 작성 진행중"
                    current_context.add("STORY")
                elif msg.get("message_type") == MessageType.RECEIPT_SUMMARY.value and "RECEIPT" not in current_context:
                    context_summary += "\n- 영수증 분석 진행중"
                    current_context.add("RECEIPT")

            # 일반 대화를 위한 프롬프트 구성
            prompt = f"""
                [시스템 역할]
                당신은 Analog To Digital(A2D) 서비스의 AI 어시스턴트입니다. 
                사용자와의 대화를 기반으로 다양한 형태의 문서를 생성하고, 
                음성 파일, 이미지, 문서를 관리하는 역할을 합니다.

                [컨텍스트 정보]
                - 사용자 파일 수: {len(files)}개
                - 파일 목록: {', '.join(f['title'] for f in files)}{context_summary}
                {ocr_context}

                [사용자 질문]
                {query}

                [응답 가이드라인]
                1. 스토리 생성 시:
                - 네 알겠습니다 등의 대답은 생략합니다.
                - 바로 스토리 본문을 시작합니다.
                - 부가 설명이나 맺음말을 넣지 않습니다.
                
                2. 일반 대화 시:
                - 자연스러운 대화를 이어갑니다.
                - 필요한 경우 서비스 기능을 안내합니다.
            """

            response = chat.send_message(prompt)
            
            if save_to_history:
                # 사용자 메시지 저장
                await self.save_chat_message(user_id, "user", query)

                # 일반 대화 타입 판단
                message_type = MessageType.GENERAL
                if ocr_data:
                    message_type = MessageType.RECEIPT_SUMMARY
                elif any(keyword in query.lower() for keyword in ["이걸", "스토리", "이야기", "소설", "글쓰기"]):
                    message_type = MessageType.BOOK_STORY

                await self.save_chat_message(
                    user_id,
                    "model",
                    response.text,
                    message_type,
                    data={
                        # sequel 여부 (만약 intention이 SEQUEL이면 True)
                        "original_title": None,
                        "is_sequel": False
                    }
                )

            return {
                "type": "chat",
                "message": response.text,
                "data": None
            }

        except Exception as e:
            logger.error(f"Query processing error: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Query processing failed: {str(e)}"
            )
