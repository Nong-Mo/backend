# app/utils/query_util.py

import json
import re
import logging
import datetime
import difflib
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
        return filename.replace("'", "").replace('"', "").replace(" ", "")

    def evaluate_snippet(self, snippet: str) -> bool:
        # 스니펫이 일정 길이 이상이고 문장부호가 있으면 문장이라고 간주
        return len(snippet) > 15 and any(p in snippet for p in ".!?")

    async def refine_snippet_with_llm(self, snippet: str, query: str) -> str:
        prompt = f"""
        아래 텍스트는 검색 결과에서 추출된 비문장적 내용입니다. 이를 자연스러운 문장으로 보정해주세요.
        - 검색어: {query}
        - 추출된 스니펫: {snippet}

        주의사항:
        1. 검색어와 맥락을 유지하며 보정하세요.
        2. 1~2문장으로 구성된 완전한 문장으로 수정하세요.
        """
        response = self.model.send_message(prompt)
        return response.text.strip()

    async def refine_and_correct_snippets(self, snippets: List[str], query: str) -> List[str]:
        refined_snippets = []
        for snippet in snippets:
            if self.evaluate_snippet(snippet):
                refined_snippet = f"...{snippet.strip()}..."
            else:
                refined_snippet = await self.refine_snippet_with_llm(snippet, query)
            refined_snippets.append(refined_snippet)
        return refined_snippets

    def extract_snippets(self, text: str, query: str, snippet_length: int = 30, max_snippets: int = 3) -> list:
        pattern = re.compile(f'(?i)(.{{0,{snippet_length}}}{re.escape(query)}.{{0,{snippet_length}}})')
        matches = pattern.findall(text)
        return matches[:max_snippets]

    async def search_file(self, user_id: str, query: str) -> Dict[str, Any]:
        try:
            user = await self.users_collection.find_one({"email": user_id})
            if not user:
                return {
                    "type": "error",
                    "message": "사용자를 찾을 수 없습니다.",
                    "data": None
                }

            search_query = {
                "user_id": user["_id"],
                "$or": [
                    {"title": {"$regex": query, "$options": "i"}},
                    {"contents": {"$regex": query, "$options": "i"}}
                ]
            }

            files = await self.files_collection.find(search_query).to_list(length=None)
            if not files:
                all_titles = await self.files_collection.distinct("title", {"user_id": user["_id"]})
                close_matches = difflib.get_close_matches(query, all_titles, n=3, cutoff=0.7)
                if close_matches:
                    suggested_title = close_matches[0]
                    return {
                        "type": "no_results",
                        "message": f"'{query}' 대신 '{suggested_title}'(으)로 검색하시겠어요?",
                        "data": {"suggestions": close_matches}
                    }
                return {
                    "type": "no_results",
                    "message": f"'{query}'와 관련된 파일을 찾을 수 없습니다.",
                    "data": None
                }

            result_data = []
            for file in files:
                content = file.get("contents", "")
                if not isinstance(content, str):
                    content = str(content) if content else ""

                raw_snippets = self.extract_snippets(content, query, snippet_length=30, max_snippets=3)
                refined_snippets = await self.refine_and_correct_snippets(raw_snippets, query)

                if refined_snippets:
                    result_data.append({
                        "file_id": str(file["_id"]),
                        "title": file.get("title", "제목없음"),
                        "snippets": refined_snippets
                    })

            if not result_data:
                return {
                    "type": "no_results",
                    "message": f"'{query}'와 관련된 정확한 내용이 없습니다.",
                    "data": None
                }

            if len(result_data) == 1:
                single_file = result_data[0]
                message = (
                    f"'{single_file['title']}' 파일에서 아래 내용을 찾았습니다:\n\n"
                    + "\n".join([f"- \"{snippet}\"" for snippet in single_file["snippets"]])
                    + "\n\n혹시 이 내용들 중에서 찾으시는 정보가 있으신가요?"
                )
            else:
                message = (
                    "다음 파일들에서 관련 내용을 찾았습니다:\n"
                    + "\n".join(
                        f"- {finfo['title']}:\n  " + "\n  ".join([f"- \"{s}\"" for s in finfo["snippets"]])
                        for finfo in result_data
                    )
                    + "\n\n어느 파일이 맞는지 선택해주세요."
                )

            return {"type": "file_found", "message": message, "data": {"files": result_data}}

        except Exception as e:
            logger.error(f"[search_file] 검색 오류: {str(e)}", exc_info=True)
            return {
                "type": "error",
                "message": "파일 검색 중 오류가 발생했습니다.",
                "data": None
            }

    async def save_chat_message(self, user_id: str, role: str, content: str | dict,
                                message_type: MessageType = MessageType.GENERAL,
                                data: Dict = None):
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
        user = await self.users_collection.find_one({"email": user_id})
        if not user:
            return []
        return await self.files_collection.find({"user_id": user["_id"]}).to_list(length=None)

    def classify_intention_once(self, user_query: str) -> str:
        """
        (1회성) 사용자 메시지를 Google Generative AI에 보냄
        -> model="gemini-2.0-flash-exp" 등, generate_text(...) 함수로 태그만 분류해옴
        """
        prompt = f"""
        사용자 메시지를 보고, 다음 중 하나만 *그대로* 출력하세요:

        1) SEARCH:파일명
        2) SEQUEL:파일명
        3) SAVE
        4) STORY
        5) SUMMARY:파일명
        6) ANALYSIS:파일명
        7) REVIEW:파일명
        8) CHAT

        [규칙]
        - 딱 위 형식 그대로만 출력하세요. 다른 말은 일절 하지 마세요.
        - 파일명 추출 시, 불필요한 조사('에서', '파일', '관련' 등) 제거.
        - 파일명이 없으면 CHAT으로 처리.

        사용자 메시지: "{user_query}"
        """
        chat_session = genai.ChatSession(model="gemini-2.0-flash-exp")   
        try:
            response = chat_session.send_message(prompt=prompt)  # 프롬프트 전달
        except Exception as e:
            logger.error(f"Error during intention classification: {e}")
            return "CHAT"

        if not response or not hasattr(response, "text") or not response.text:
            return "CHAT"

        raw_text = response.text.strip()  # 응답 텍스트 정리
        logger.debug(f"[Intention classification result] {raw_text}")
        return raw_text

    async def process_query(self, user_id: str, query: str, new_chat: bool = False, save_to_history: bool = True):
        """
        사용자 질의를 처리:
          1) 로컬 규칙("저장"/"save") 우선
          2) 1회성 predict(...)로 의도만 분류 (CHAT, SEARCH:..., etc)
          3) 의도에 따라 분기
          4) CHAT인 경우에만 chat.send_message(...)로 최종 대화 생성
        """
        try:
            # (A) 로컬 규칙: "저장"/"save" 단어가 포함되면 즉시 저장 로직
            lower_query = query.lower()
            if ("저장" in lower_query) or ("save" in lower_query):
                logger.info("[Local Rule] '저장' or 'save' detected in user query.")
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

            # (B) 기존 대화 이력 & 세션 확보
            chat_history = await self.get_chat_history(user_id)
            if new_chat or user_id not in self.chat_sessions:
                self.chat_sessions[user_id] = self.model.start_chat(
                    history=[] if new_chat else chat_history
                )
            chat = self.chat_sessions[user_id]

            # (C) 1회성 의도 분류 (챗 세션 사용 X)
            intention_text = self.classify_intention_once(query)
            normalized_intent = intention_text.lower()

            # (D) 의도별 처리

            # 1. SEARCH
            if intention_text.startswith("SEARCH:"):
                search_keyword = intention_text.split("SEARCH:", 1)[1].strip()
                search_result = await self.search_file(user_id, search_keyword)

                if search_result["type"] == "file_found":
                    file_data = search_result["data"]
                    if "files" in file_data:
                        formatted_files = "\n".join([
                            f"파일 제목: {f['title']}\n발췌 내용:\n" 
                            + "\n".join([f"- {snippet}" for snippet in f['snippets']])
                            for f in file_data['files']
                        ])
                        llm_prompt = f"""
                        사용자가 '{search_keyword}'를 검색했습니다.
                        다음 파일들에서 관련 내용을 찾았습니다:

                        {formatted_files}

                        아래 지침:
                        1. 각 파일 제목과 발췌 내용을 그대로 보여주세요.
                        2. 이 파일들 중 찾으시는 내용이 있는지 물어보세요.
                        3. 메시지 길이는 3~5문장 내외로 작성하세요.
                        """
                    else:
                        # 혹시 단일 파일
                        title = file_data.get("title", "")
                        snippets = file_data.get("snippets", [])
                        formatted_snippets = "\n".join([f"- {s}" for s in snippets])
                        llm_prompt = f"""
                        사용자가 '{search_keyword}'를 검색했습니다.
                        아래 파일 '{title}'에서 발췌 내용이 발견되었습니다:

                        {formatted_snippets}

                        지침:
                        1. 발췌 내용 정확히 전달
                        2. "이 책이 맞습니까?" 부드럽게 질문
                        3. 3~5문장 내외
                        """
                    refined_message = chat.send_message(llm_prompt).text.strip()
                    search_result["message"] = refined_message
                elif search_result["type"] == "no_results":
                    search_result["message"] = (
                        f"'{search_keyword}'와 관련된 파일을 찾을 수 없습니다. "
                        f"다른 키워드로 검색하거나 정확히 입력해주세요."
                    )

                if save_to_history:
                    # 대화 저장
                    await self.save_chat_message(user_id, "user", query)
                    await self.save_chat_message(user_id, "model", search_result["message"], MessageType.GENERAL)

                return search_result

            # 2. SEQUEL
            elif intention_text.startswith("SEQUEL:"):
                title = intention_text.split("SEQUEL:", 1)[1].strip()
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
                story_content = file['contents'] if isinstance(file['contents'], str) else file['contents'].get('text','')

                sequel_prompt = f"""
                [원본 이야기 제목]
                {file['title']}

                [원본 이야기 내용]
                {story_content}

                [규칙]
                - 300자 내외 뒷이야기
                - 부가설명 없이 이야기로만
                - 기존 인물과 세계관 유지

                [사용자 메시지]
                {query}
                """
                response = chat.send_message(sequel_prompt)
                if save_to_history:
                    await self.save_chat_message(user_id, "user", query)
                    await self.save_chat_message(user_id, "model", response.text, MessageType.BOOK_STORY)

                return {
                    "type": "chat",
                    "message": response.text,
                    "data": {
                        "original_title": file['title'],
                        "is_sequel": True
                    }
                }

            # 3. SAVE
            elif intention_text == "SAVE":
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

            # 4. STORY
            elif intention_text == "STORY":
                ocr_data = None
                for msg in reversed(chat_history):
                    if isinstance(msg.get("content"), dict) and msg.get("type") == "ocr_result":
                        ocr_data = msg["content"]
                        break
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
                    await self.save_chat_message(user_id, "model", response.text, MessageType.BOOK_STORY)

                return {
                    "type": "chat",
                    "message": response.text,
                    "data": None
                }

            # 5. SUMMARY: 요약
            elif intention_text.startswith("SUMMARY:"):
                file_name = intention_text.split("SUMMARY:", 1)[1].strip()
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
                file_content = file['contents'] if isinstance(file['contents'], str) else file['contents'].get('text','')

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
                    await self.save_chat_message(user_id, "model", response.text, MessageType.GENERAL)

                return {
                    "type": "chat",
                    "message": response.text,
                    "data": None
                }

            # 6. REVIEW: 서평
            elif intention_text.startswith("REVIEW:"):
                file_name = intention_text.split("REVIEW:", 1)[1].strip()
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
                file_content = file['contents'] if isinstance(file['contents'], str) else file['contents'].get('text','')

                review_prompt = f"""
                [시스템 역할]
                당신은 A2D 서비스의 AI 서평 작성 어시스턴트입니다.
                200자 이내로 서평을 작성해주세요.

                [파일 이름]
                {file_name}

                [파일 내용]
                {file_content}

                [사용자 메시지]
                {query}
                """
                response = chat.send_message(review_prompt)
                if save_to_history:
                    await self.save_chat_message(user_id, "user", query)
                    await self.save_chat_message(user_id, "model", response.text, MessageType.GENERAL)

                return {
                    "type": "chat",
                    "message": response.text,
                    "data": None
                }
            
            elif intention_text.startswith("ANALYSIS:"):
                # 파일명 추출
                file_name = intention_text.split("ANALYSIS:", 1)[1].strip()
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
                file_content = file['contents'] if isinstance(file['contents'], str) else file['contents'].get('text','')

                review_prompt = f"""
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
                """
                response = chat.send_message(review_prompt)
                if save_to_history:
                    await self.save_chat_message(user_id, "user", query)
                    await self.save_chat_message(user_id, "model", response.text, MessageType.GENERAL)

                return {
                    "type": "chat",
                    "message": response.text,
                    "data": None
                }

            # 7. BLOG: 블로그 작성
            elif intention_text.startswith("BLOG:"):
                file_name = intention_text.split("BLOG:", 1)[1].strip()
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
                file_content = file['contents'] if isinstance(file['contents'], str) else file['contents'].get('text','')

                blog_prompt = f"""
                [시스템 역할]
                당신은 A2D 서비스의 AI 블로그 작성 어시스턴트입니다.
                아래 파일 내용을 바탕으로 기술 블로그 초안을 작성하세요.

                [파일 이름]
                {file_name}

                [파일 내용]
                {file_content}

                [사용자 메시지]
                {query}
                """
                response = chat.send_message(blog_prompt)
                if save_to_history:
                    await self.save_chat_message(user_id, "user", query)
                    await self.save_chat_message(user_id, "model", response.text, MessageType.GENERAL)

                return {
                    "type": "chat",
                    "message": response.text,
                    "data": None
                }

            # (E) 의도에 '저장' 남아있을 경우 다시 SAVE 로직
            if ("저장" in normalized_intent) or ("save" in normalized_intent):
                logger.info("[Partial Parse] Found '저장'/'save' in classification text.")
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
            # 사용자 정보 가져오기
            user = await self.users_collection.find_one({"email": user_id})
            if not user:
                return {
                    "type": "error",
                    "message": "사용자 정보를 찾을 수 없습니다.",
                    "data": None
                }

            # 닉네임 가져오기 (기본값은 "사용자")
            nickname = user.get("nickname", "사용자")

            # (F) 일반 대화 (CHAT)
            ocr_data = None
            for msg in reversed(chat_history):
                if isinstance(msg.get("content"), dict) and msg.get("type") == "ocr_result":
                    ocr_data = msg["content"]
                    await self.save_chat_message(user_id, "user", ocr_data, MessageType.RECEIPT_RAW)
                    break

            files = await self.get_user_files(user_id)
            ocr_context = ""
            if ocr_data:
                ocr_context = f"\n\n[OCR 분석 결과]\n{json.dumps(ocr_data, ensure_ascii=False, indent=2)}"

            # 닉네임을 포함한 프롬프트 구성
            final_prompt = f"""
            [시스템 역할]
            당신은 A2D 서비스의 AI 어시스턴트입니다.
            아래 사용자 메시지에 대해 자유롭게 대답하세요.
            다만 사용자의 DB에 저장된 nickname인 '{nickname}'을 반드시 언급하세요.

            [사용자 메시지]
            "{query}"

            {ocr_context}
            """
            # 프롬프트 전송 및 응답 받기
            response = chat.send_message(final_prompt)
            if save_to_history:
                await self.save_chat_message(user_id, "user", query)
                await self.save_chat_message(user_id, "model", response.text, MessageType.GENERAL)

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