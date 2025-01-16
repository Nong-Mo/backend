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
        사용자 메시지를 분석해 단 하나의 의도만을 정확히 분류합니다.
        """
        # 공통 설정
        search_keywords = ["찾아", "검색", "알려줘"]
        operations = {
            "분석": "ANALYSIS",
            "요약": "SUMMARY",
            "서평": "REVIEW",
            "블로그": "BLOG",
        }
        story_keywords = ["스토리", "이야기", "소설"]
        skip_words = [
            "좀", "해줘", "주세요", "해", "을", "를", "가지고",
            "작성", "으로", "로", "에", "관련", "내용", "에서", "에 대해", "의"
        ]
        file_extensions = [".txt", ".pdf", ".docx"]

        def extract_main_subject(user_query: str, skip_words: list) -> str:
            """주요 대상을 추출"""
            # 문장을 단어로 분리하고 조사와 불용어를 제외
            words = [
                word.strip()
                for word in re.split(r"[ ,]", user_query)
                if word and word not in skip_words
            ]
            return words[0] if words else ""

        # 1. 검색 의도 확인
        if any(keyword in user_query for keyword in search_keywords):
            subject = extract_main_subject(user_query, skip_words + search_keywords)
            if subject:
                return f"SEARCH:{subject}"

        # 2. 파일 관련 작업 확인
        for op, intent in operations.items():
            if op in user_query:
                subject = extract_main_subject(user_query, skip_words + [op])
                if subject:
                    return f"{intent}:{subject}"

        # 3. 저장 의도 확인
        if "저장" in user_query:
            return "SAVE"

        # 4. 이어쓰기 의도 확인
        if "이어서" in user_query:
            words = user_query.split()
            target_file = ""
            for i, word in enumerate(words):
                if "이어서" in word:
                    if i > 0:
                        target_file = words[i - 1]
                    elif i + 1 < len(words):
                        target_file = words[i + 1]
                    break
            if target_file:
                return f"SEQUEL:{target_file}"

        # 5. 스토리 생성 의도 확인
        if any(keyword in user_query for keyword in story_keywords):
            return "STORY"

        # 6. 기본값
        return "CHAT"

    async def get_inspiration_contents(self, user_id: str):
        try:
            user = await self.users_collection.find_one({"email": user_id})
            if not user:
                return []

            # 먼저 "영감" 보관함 찾기
            inspiration_storage = await self.db.storages.find_one({
                "user_id": user["_id"],
                "name": "영감"
            })

            if not inspiration_storage:
                return []

            # 해당 보관함의 파일들 조회
            files = await self.files_collection.find({
                "storage_id": inspiration_storage["_id"]
            }).to_list(length=None)

            return [
                {
                    "title": file.get("title", ""),
                    "content": file.get("contents", "")
                }
                for file in files
            ]
        except Exception as e:
            logger.error(f"영감 보관함 조회 실패: {str(e)}")
            return []

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

                        지침:
                        1. 반드시 각 파일 제목과 발췌 내용을 그대로 보여주세요.
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
                try:
                    # 1. 영감 보관함 콘텐츠 조회 전에 유효성 검사
                    user = await self.users_collection.find_one({"email": user_id})
                    if not user:
                        return {
                            "type": "error",
                            "message": "사용자 정보를 찾을 수 없습니다.",
                            "data": None
                        }

                    # 2. 영감 보관함 존재 여부 확인
                    inspiration_storage = await self.db.storages.find_one({
                        "user_id": user["_id"],
                        "name": "영감"
                    })

                    if not inspiration_storage:
                        return {
                            "type": "error",
                            "message": "영감 보관함이 없습니다. 먼저 영감 보관함을 생성해주세요.",
                            "data": None
                        }

                    # 3. 영감 보관함 콘텐츠 조회
                    inspiration_contents = await self.get_inspiration_contents(user_id)

                    if not inspiration_contents:
                        return {
                            "type": "error",
                            "message": "영감 보관함이 비어있습니다. 먼저 몇 가지 영감을 저장해주세요.",
                            "data": None
                        }

                    # 4. 콘텐츠 유효성 검사 및 포매팅
                    valid_contents = []
                    for i, content in enumerate(inspiration_contents):
                        if content.get('content') and content.get('title'):
                            valid_contents.append({
                                'index': i + 1,
                                'title': content['title'],
                                'content': content['content'].strip()
                            })

                    if not valid_contents:
                        return {
                            "type": "error",
                            "message": "영감 보관함에 유효한 내용이 없습니다.",
                            "data": None
                        }

                    # 5. 영감 내용을 포맷팅
                    contents_text = "\n\n".join([
                        f"[영감 {content['index']}]\n제목: {content['title']}\n내용: {content['content']}"
                        for content in valid_contents
                    ])

                    # 6. 개선된 스토리 프롬프트
                    story_prompt = f"""
                                    [시스템 역할]
                                    당신은 A2D 서비스의 '부트캠프 스토리텔러'입니다!
                                    사용자의 실제 경험과 영감을 코딩 부트캠프에서의 성장기로 재구성하는 작가입니다.
                                    개발자 교육과정에서의 도전, 열정, 성장을 진정성 있게 담아내주세요.

                                    [저장된 실제 내용들]
                                    {contents_text}

                                    [스토리 통합 규칙]
                                    1. 부트캠프 성장기로 재구성:
                                       - 위 영감 중 최소 3개를 교육과정 스토리로 통합
                                       - 각 영감을 개발자로서의 성장 이정표로 승화
                                       - 실제 경험을 학습과 성장의 과정으로 표현

                                    2. 역동적인 서사 구조:
                                       - 입과-성장-도약의 3막 구조
                                       - 영감들이 개발자 성장의 터닝포인트로 연결
                                       - 시행착오와 깨달음을 통한 성장 스토리

                                    3. 현실적인 테마:
                                       - 알고리즘 학습을 '산 넘어 산' 과정으로 표현
                                       - 버그와 에러를 성장을 위한 시련으로 묘사
                                       - 팀 프로젝트를 통한 협업과 성장 강조
                                       - 개발자 교육과정의 특색있는 문화 반영

                                    4. 공감대 높은 글쓰기:
                                       - 성장과 도전이 묻어나는 문체
                                       - 개발 용어와 교육과정 용어의 자연스러운 활용
                                       - 모든 예비 개발자가 공감할 수 있는 상황 연출
                                       - 적절한 긴장감과 유머로 몰입도 유지

                                    [캐릭터 설정]
                                    - 주인공: 비전공자 출신의 '열정 개발자'
                                    - 동료들: 다양한 배경의 교육생들
                                      - 든든한 조력자 멘토님
                                      - 밤샘 스터디의 동지들
                                      - 서로 의지하는 팀원들
                                    - 도전과제: 난이도 높은 과제, 팀 프로젝트, 취업 준비

                                    [스토리 가이드]
                                    - 개발자 입문자의 성장통과 극복 과정
                                    - 알고리즘과 프로젝트의 구체적 어려움
                                    - 동료들과의 협업을 통한 문제 해결
                                    - 실패를 두려워하지 않는 도전 정신

                                    [출력 형식]
                                    제목: (공감되는 부트캠프 성장기 제목)
                                    (한 줄 띄우기)
                                    본문: (500자 내외의 개발자 성장 스토리)

                                    [사용자 메시지]
                                    {query}
                                    """

                    # 7. LLM 응답 생성 및 저장
                    response = chat.send_message(story_prompt)

                    if save_to_history:
                        await self.save_chat_message(user_id, "user", query)
                        await self.save_chat_message(
                            user_id,
                            "model",
                            response.text,
                            MessageType.BOOK_STORY,
                            {"inspiration_count": len(valid_contents)}
                        )

                    return {
                        "type": "story",
                        "message": response.text,
                        "data": {
                            "inspiration_count": len(valid_contents),
                            "used_inspirations": [content['title'] for content in valid_contents[:3]]
                        }
                    }

                except Exception as e:
                    logger.error(f"Error processing story request: {str(e)}")
                    return {
                        "type": "error",
                        "message": f"스토리 생성 중 오류가 발생했습니다: {str(e)}",
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
                    "type": "summary",
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
                    "type": "review",
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
                대괄호[ ] 안에 있는 건 출력하지 않습니다.

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

                """
                response = chat.send_message(review_prompt)
                if save_to_history:
                    await self.save_chat_message(user_id, "user", query)
                    await self.save_chat_message(user_id, "model", response.text, MessageType.GENERAL)

                return {
                    "type": "analysis",
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
                    "type": "blog",
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