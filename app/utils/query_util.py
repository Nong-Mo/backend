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

    def normalize_filename(self, filename: str) -> str:
        # 따옴표, 공백 제거
        return filename.replace("'", "").replace('"', "").replace(" ", "")

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
                {"title": {"$regex": query, "$options": "i"}},  # 대소문자 무시
                {"contents": {"$regex": query, "$options": "i"}}  # 내용 검색
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

            4. 스토리 창작 의도:
            - "소설 써줘", "이야기 만들어줘"
            - "스토리 작성해줘", "글 써줘"
            - "영감으로 이야기 만들어줘"

            5. 요약/분석 의도:
            - 파일명이 언급되고 ("크래프톤 웨이", '크래프톤 웨이', 크래프톤 웨이 등)
            - "중요한 내용", "핵심 내용", "가장 중요한" 등의 표현이 있는 경우

            6. 서평 작성 의도:
            - "서평", "리뷰", "감상" 등의 단어가 포함된 경우
            - "100자 이내로" 등 길이 제한과 함께 요청되는 경우

            파일명 처리 규칙:
            - '크래프톤 웨이', "크래프톤 웨이", 크래프톤 웨이 등의 다양한 형태로 입력된 파일명을 처리
            - "에서", "의", "파일" 등의 조사나 부가 단어는 제거하고 파일명만 추출
            

            응답 형식은 반드시 아래 중 하나로만:
            - 파일 검색이면: SEARCH:파일명
            - 뒷이야기 요청이면: SEQUEL:파일명
            - 저장 요청이면: SAVE
            - 스토리 창작이면: STORY
            - 요약이면: SUMMARY:파일명
            - 서평이면: REVIEW:파일명
            - 위 경우가 아닌 일반 대화면: CHAT

            
            주의사항:
            - "서평 작성해줘", "간단한 서평" 등 파일명 없이 서평 요청이 오면
            이전에 다룬 파일("크래프톤 웨이")에 대한 요청으로 간주합니다.
            - "200자 이내" 등의 요청도 이전 파일에 대한 서평 요청으로 처리합니다

            예시:
            - '크래프톤 웨이'에서 중요한 내용 알려줘 -> SUMMARY:크래프톤 웨이
            - 이어서 서평 작성해줘 -> REVIEW:크래프톤 웨이
            - 간단한 서평 부탁해 -> REVIEW:크래프톤 웨이

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
                이야기는 300자 정도로 작성해주세요.
                
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
            
            elif intention_response.text == "STORY":
                # 일반 대화 처리와 동일한 컨텍스트 정보 수집
                ocr_data = None
                for msg in reversed(chat_history):
                    if isinstance(msg.get("content"), dict) and msg.get("type") == "ocr_result":
                        ocr_data = msg["content"]
                        break

                files = await self.get_user_files(user_id)

                # 스토리 창작용 프롬프트
                story_prompt = f"""
                    [시스템 역할]
                    당신은 A2D 서비스의 AI 창작 어시스턴트입니다.
                    스캔한 텍스트와 메모에서 영감을 받아 200자 내외의 소설 도입부를 창작합니다.

                    [창작 자료]
                    1. 스캔한 책 발췌문:
                    "그래서 저는 실패가 두렵지 않습니다. 여러분이 일정 수준을 넘어서는 결과물을 반드시 내줄 것이란 믿음이 있습니다.
                    시장에서의 성패는 다른 문제입니다. 실패하더라도 과정이 아름답다면 의미가 있습니다. 최선을 다한다면, 그것이 아름다운 것입니다."

                    2. 화이트보드 메모:
                    "대충은 가장 해로운 벌레다."

                    3. 영감 키워드:
                    - 장병규 의장
                    - 맥주
                    - 에러 코드

                    [응답 가이드라인]
                    - 네 알겠습니다 등의 부가 설명 생략
                    - 제목을 포함하여 바로 본문 시작
                    - 세 가지 창작 자료를 모두 활용
                    - "대충은 가장 해로운 벌레다" 문구를 결말에 자연스럽게 포함
                    - 오디오북 전환을 고려한 명확한 문장 구성

                    [사용자 질문]
                    {query}
                """

                response = chat.send_message(story_prompt)
                
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
                    "data": None
                }

            # STORY 처리 다음에 추가
            elif intention_response.text.startswith("ANALYSIS:"):
                # 파일명 추출
                file_name = intention_response.text.split("ANALYSIS:", 1)[1].strip()
                
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
                    "$or": [
                        {"title": file_name},
                        {"title": file_name.replace(" ", "")},
                        {"title": {"$regex": f".*{file_name}.*", "$options": "i"}}
                    ]
                })
                
                if not file:
                    return {
                        "type": "error",
                        "message": f"'{file_name}' 파일을 찾을 수 없습니다.",
                        "data": None
                    }

                # 파일 내용 가져오기
                file_content = file['contents'] if isinstance(file['contents'], str) else file['contents'].get('text', '')

                # 분석/요약용 프롬프트
                analysis_prompt = f"""
                    [시스템 역할]
                    당신은 A2D 서비스의 AI 분석 어시스턴트입니다.
                    파일의 내용을 분석하고 요약하여 핵심 메시지를 추출합니다.

                    [분석 대상 파일]
                    제목: {file_name}
                    내용: {file_content}

                    [분석 대상 텍스트]
                    {file_content}

                    [분석 가이드라인]
                    1. 핵심 메시지 (50자 이내):
                    - 텍스트의 본질적 의미를 함축적으로 표현
                    - 리더의 철학이나 가치관이 드러나도록 구성
                    
                    2. 주요 인사이트 (각 30자 이내):
                    - 리더십 관점의 시사점
                    - 조직 문화적 시사점
                    - 개인 성장 관점의 시사점
                    
                    3. 실전 적용점 (각 50자 이내):
                    - 조직 리더의 관점에서 적용 방안
                    - 팀 구성원의 관점에서 적용 방안
                    - 개인의 성장 관점에서 적용 방안

                    4. 블로그 콘텐츠용 주제 추천 (3가지):
                    - 리더십 관련 주제
                    - 조직 문화 관련 주제
                    - 개인 성장 관련 주제

                    [응답 형식]
                    1. 핵심 메시지: (내용)

                    2. 주요 인사이트:
                    - 리더십: (내용)
                    - 조직문화: (내용)
                    - 개인성장: (내용)

                    3. 실전 적용점:
                    - 리더: (내용)
                    - 팀원: (내용)
                    - 개인: (내용)

                    4. 추천 블로그 주제:
                    - 리더십: (제목)
                    - 조직문화: (제목)
                    - 개인성장: (제목)

                    [사용자 질문]
                    {query}
                """

                response = chat.send_message(analysis_prompt)
                
                if save_to_history:
                    await self.save_chat_message(user_id, "user", query)
                    await self.save_chat_message(
                        user_id,
                        "model",
                        response.text,
                        MessageType.GENERAL
                    )

                return {
                    "type": "chat",
                    "message": response.text,
                    "data": None
                }


            # STORY 처리 다음에, 일반 대화 처리 전에 아래 코드들을 추가

            # (2-5) 요약 요청 처리
            elif intention_response.text.startswith("SUMMARY:"):
                # 파일명 추출
                file_name = intention_response.text.split("SUMMARY:", 1)[1].strip()
                
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
                    "$or": [
                        {"title": file_name},
                        {"title": file_name.replace(" ", "")},
                        {"title": {"$regex": f".*{file_name}.*", "$options": "i"}}
                    ]
                })
                
                if not file:
                    return {
                        "type": "error",
                        "message": f"'{file_name}' 파일을 찾을 수 없습니다.",
                        "data": None
                    }

                # 파일 내용 가져오기
                file_content = file['contents'] if isinstance(file['contents'], str) else file['contents'].get('text', '')

                # 요약용 프롬프트
                summary_prompt = f"""
                    [시스템 역할]
                    당신은 A2D 서비스의 AI 분석 어시스턴트입니다.
                    파일의 내용에서 핵심 메시지만을 간단명료하게 추출합니다.

                    [분석 대상 파일]
                    제목: {file_name}
                    내용: {file_content}

                    [요약 가이드라인]
                    - 50자 이내로 핵심 메시지 한 줄 추출
                    - 저자의 핵심 주장이나 가치관이 드러나도록
                    - 실용적이고 적용 가능한 관점으로 요약

                    [사용자 질문]
                    {query}
                """

                response = chat.send_message(summary_prompt)
                
                if save_to_history:
                    await self.save_chat_message(user_id, "user", query)
                    await self.save_chat_message(
                        user_id,
                        "model",
                        response.text,
                        MessageType.GENERAL
                    )

                return {
                    "type": "chat",
                    "message": response.text,
                    "data": None
                }

            # (2-6) 서평 요청 처리
            elif intention_response.text.startswith("REVIEW:"):
                # 파일명 추출
                file_name = intention_response.text.split("REVIEW:", 1)[1].strip()
                
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
                    "$or": [
                        {"title": file_name},
                        {"title": file_name.replace(" ", "")},
                        {"title": {"$regex": f".*{file_name}.*", "$options": "i"}}
                    ]
                })
                
                if not file:
                    return {
                        "type": "error",
                        "message": f"'{file_name}' 파일을 찾을 수 없습니다.",
                        "data": None
                    }

                # 파일 내용 가져오기
                file_content = file['contents'] if isinstance(file['contents'], str) else file['contents'].get('text', '')

                # 서평용 프롬프트
                review_prompt = f"""
                    [시스템 역할]
                    당신은 A2D 서비스의 AI 서평 작성 어시스턴트입니다.
                    200자 이내의 간단한 서평을 작성합니다.

                    [분석 대상 파일]
                    제목: {file_name}
                    내용: {file_content}

                    [서평 가이드라인]
                    - 200자 이내의 문장으로 구성
                    - 책의 가치와 의의를 중심으로
                    - 잠재적 독자에게 도움이 될 인사이트 포함

                    [사용자 질문]
                    {query}
                """

                response = chat.send_message(review_prompt)
    
                if save_to_history:
                    await self.save_chat_message(user_id, "user", query)
                    await self.save_chat_message(
                        user_id,
                        "model",
                        response.text,
                        MessageType.GENERAL
                    )

                return {
                    "type": "chat",
                    "message": response.text,
                    "data": None
                }
                
                # 히스토리 저장 및 응답 반환 로직은 위와 동일...

            # (2-7) 블로그 작성 요청 처리
            elif intention_response.text.startswith("BLOG:"):
                # 파일명 추출
                file_name = intention_response.text.split("BLOG:", 1)[1].strip()
                
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
                    "$or": [
                        {"title": file_name},
                        {"title": file_name.replace(" ", "")},
                        {"title": {"$regex": f".*{file_name}.*", "$options": "i"}}
                    ]
                })                
                            
                if not file:
                    return {
                        "type": "error",
                        "message": f"'{file_name}' 파일을 찾을 수 없습니다.",
                        "data": None
                    }

                # 파일 내용 가져오기
                file_content = file['contents'] if isinstance(file['contents'], str) else file['contents'].get('text', '')

                # 블로그용 프롬프트
                blog_prompt = f"""
                    [시스템 역할]
                    당신은 A2D 서비스의 AI 블로그 작성 어시스턴트입니다.
                    기술 블로그에 적합한 콘텐츠를 작성합니다.

                    [분석 대상 파일]
                    제목: {file_name}
                    내용: {file_content}

                    [블로그 작성 가이드라인]
                    1. 추천 주제 (3개):
                    - 리더십 관점의 주제
                    - 조직 문화 관점의 주제
                    - 개인 성장 관점의 주제

                    2. 각 주제별:
                    - 제목
                    - 주요 논점 3가지
                    - 실제 적용 방안 2가지

                    [사용자 질문]
                    {query}
                """

                response = chat.send_message(blog_prompt)
    
                if save_to_history:
                    await self.save_chat_message(user_id, "user", query)
                    await self.save_chat_message(
                        user_id,
                        "model",
                        response.text,
                        MessageType.GENERAL
                    )

                return {
                    "type": "chat",
                    "message": response.text,
                    "data": None
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
            # 일반 대화를 위한 프롬프트 구성 부분 업데이트
            prompt = f"""
                [시스템 역할]
                당신은 Analog To Digital(A2D) 서비스의 AI 어시스턴트입니다.
                1. 전문가용 콘텐츠: 스캔한 텍스트를 분석하고, 기술 블로그와 발표 자료 작성을 돕습니다.
                2. 창작 콘텐츠: 스캔한 자료에서 영감을 받아 소설과 이야기를 창작합니다.

                [컨텍스트 정보]
                - 사용자 파일 수: {len(files)}개
                - 파일 목록: {', '.join(f['title'] for f in files)}{context_summary}
                {ocr_context}

                [주요 기능 가이드라인]
                1. 전문 콘텐츠 지원
                - 책/문서의 핵심 문장 추출
                - 100자 이내의 서평 작성
                - 기술 블로그 주제 추천 및 초안 작성
                - 발표 자료용 핵심 문구 추출

                2. 창작 소설 지원
                - 스캔한 자료들을 영감 삼아 300자 내외의 소설 창작
                - 등장인물의 생생한 캐릭터 구축
                - 인용구나 명언을 자연스럽게 스토리에 녹여내기
                - 스토리와 이야기의 의미 부여

                3. 검색 및 추천
                - 특정 주제/키워드 관련 파일 검색
                - 블로그나 소설에 적합한 소재 추천
                - 연관 자료 묶음 제안

                [응답 가이드라인]
                1. 전문 콘텐츠 작성 시:
                - 간단명료한 표현
                - 핵심 아이디어 중심
                - 전문성과 논리성 강조

                2. 소설 창작 시:
                - 네 알겠습니다 등의 부가 설명 생략
                - 제목 포함하여 바로 본문 시작
                - 등장인물과 스토리 자연스러운 전개
                - 영감이 된 문장/명언을 결말에 효과적으로 활용
                - 오디오북 전환을 고려한 문장 구성

                3. 검색/추천 시:
                - 정확한 관련 자료 제시
                - 활용 맥락 함께 제공
                - 새로운 인사이트 연결

                [창작 소설용 추가 자료]
                1. 스캔한 책 발췌문:
                "그래서 저는 실패가 두렵지 않습니다. 여러분이 일정 수준을 넘어서는 결과물을 반드시 내줄 것이란 믿음이 있습니다.
                시장에서의 성패는 다른 문제입니다. 실패하더라도 과정이 아름답다면 의미가 있습니다. 최선을 다한다면, 그것이 아름다운 것입니다.
                실패한다고 삶의 의미가 없어지는 것은 아닙니다."

                2. 화이트보드 메모:
                "대충은 가장 해로운 벌레다."

                3. 영감 키워드:
                - 장병규 의장
                - 맥주
                - 에러 코드

                [사용자 질문]
                {query}
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
