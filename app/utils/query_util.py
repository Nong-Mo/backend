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
        genai.configure(api_key=GOOGLE_API_KEY)
        self.model = genai.GenerativeModel("gemini-2.0-flash-exp")
        self.chat_sessions = {}

    async def save_chat_message(self, user_id: str, role: str, content: str | dict,
                                message_type: MessageType = MessageType.GENERAL):
        """
        채팅 메시지를 저장합니다.
        - message_type: 메시지 종류 구분
        """
        message_doc = {
            "user_id": user_id,
            "role": role,
            "content": content,
            "message_type": message_type.value,
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

    async def process_query(self, user_id: str, query: str, new_chat: bool = False, save_to_history: bool = True) -> \
    Dict[str, Any]:
        """사용자 질의를 처리하고 적절한 응답을 생성합니다."""
        try:
            # 채팅 히스토리 조회
            chat_history = await self.get_chat_history(user_id)

            # OCR 결과가 있는지 확인
            ocr_data = None
            for msg in reversed(chat_history):
                if isinstance(msg.get("content"), dict) and msg.get("type") == "ocr_result":
                    ocr_data = msg["content"]
                    # OCR 데이터를 MessageType.RECEIPT_RAW로 저장
                    await self.save_chat_message(
                        user_id,
                        "user",
                        ocr_data,
                        MessageType.RECEIPT_RAW
                    )
                    break

            # 새 채팅 세션 시작 또는 기존 세션 사용
            if new_chat or user_id not in self.chat_sessions:
                self.chat_sessions[user_id] = self.model.start_chat(
                    history=[] if new_chat else chat_history
                )

            chat = self.chat_sessions[user_id]

            # 사용자의 파일 목록 가져오기
            files = await self.get_user_files(user_id)

            # OCR 데이터가 있는 경우 컨텍스트에 포함
            ocr_context = ""
            if ocr_data:
                ocr_context = f"\n\nOCR 분석 결과:\n{json.dumps(ocr_data, ensure_ascii=False, indent=2)}"

            # 대화 맥락 추출
            context_summary = "\n현재 대화 맥락:"
            current_context = set()  # 중복 맥락 제거를 위한 set

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

            # 최근 대화 내용 포맷팅
            recent_chat = "\n\n최근 대화 내용:"
            for msg in chat_history[-5:]:  # 최근 5개 메시지
                speaker = "사용자" if msg["role"] == "user" else "AI"
                content = msg["parts"] if isinstance(msg["parts"], str) else json.dumps(msg["parts"],
                                                                                        ensure_ascii=False)
                recent_chat += f"\n{speaker}: {content}"

            # 프롬프트 구성
            prompt = f"""
                [시스템 역할]
                당신은 Audio To Document(A2D) 서비스의 AI 어시스턴트입니다. 사용자와의 대화를 기반으로 다양한 형태의 문서를 생성하고, 음성 파일, 이미지, 문서를 관리하는 역할을 합니다.
                [스토리 생성 규칙]
                1. 스토리 생성 요청을 받으면:
                - 부가적인 설명이나 인사말 없이 바로 스토리 본문으로 시작합니다.
                - 스토리가 끝나면 바로 종료합니다.
                - "어떠셨나요?", "저장하시겠습니까?" 등의 마무리 멘트를 넣지 않습니다.

                2. 일반 대화의 경우:
                - 자연스러운 대화를 진행합니다.
                - 필요한 경우 스토리 작성을 제안할 수 있습니다.
                
                [서비스 핵심 기능]
                1. 대화 기반 문서 생성:
                   - 대화 내용을 바탕으로 한 스토리/문서 생성
                   - 보관함별 맞춤형 저장:
                     * 책: 스토리를 MP3와 PDF로 변환하여 저장
                     * 영수증: OCR 분석 결과와 시각화된 보고서 생성
                     * 기타 보관함: 대화 내용을 문서화하여 저장

                2. 파일 변환 및 관리:
                   - 이미지/문서 → 음성(MP3) + PDF 변환
                   - 보관함: "책", "영수증", "굿즈", "필름 사진", "서류", "티켓"
                   - 보관함 간 파일 이동
                   - 파일 검색 및 삭제

                [컨텍스트 정보]
                - 사용자 파일 수: {len(files)}개
                - 파일 목록: {', '.join(f['title'] for f in files)}{context_summary}{recent_chat}
                {ocr_context}

                [사용자 질문]
                {query}

                [응답 가이드라인]
                1. 스토리 생성 시:
                   - 바로 스토리 본문을 시작합니다.
                   - 부가 설명이나 맺음말을 넣지 않습니다.
                   
                2. 일반 대화 시:
                   - 자연스러운 대화를 이어갑니다.
                   - 필요한 경우 서비스 기능을 안내합니다.
                3. 대화 내용 저장 요청 시:
                   - 적합한 보관함 추천
                   - 저장 가능한 형태 안내 (MP3, PDF, 텍스트 등)
                   - 제목 설정 제안
                   - 저장 후 접근 방법 설명

                4. 파일 검색 및 관리:
                   - 정확한 파일명과 위치 안내
                   - 파일 이동 옵션 제시
                   - 관련 파일 추천

                5. 분석 및 요약:
                   - 영수증: 상세 분석 및 시각화 제공
                   - 책/문서: 주요 내용 요약 및 오디오북 변환 안내
                   - 보관함별 최적화된 분석 제공

                6. 일반 응답:
                   - 한국어로 친근하게 응답
                   - 사용자의 의도 파악하여 적절한 기능 추천
                   - 명확한 단계별 안내 제공

                7. 제한사항 안내:
                   - 파일 형식 및 크기 제한
                   - 보관함별 특성에 따른 제약사항
                   - 개인정보 보호 관련 주의사항

                8. 오류 상황 대처:
                   - 파일 미발견 시 대안 제시
                   - 잘못된 요청에 대한 친절한 안내
                   - 시스템 제한사항 설명
            """

            # Gemini 모델로 응답 생성
            response = chat.send_message(prompt)
            response_text = response.text.strip()

            if save_to_history:
                # 사용자 메시지 저장
                await self.save_chat_message(
                    user_id,
                    "user",
                    query,
                    MessageType.GENERAL
                )

                # 응답 메시지 타입 결정
                message_type = MessageType.GENERAL
                if ocr_data:
                    message_type = MessageType.RECEIPT_SUMMARY
                elif any(keyword in query.lower() for keyword in ["스토리", "이야기", "소설", "글쓰기"]):
                    message_type = MessageType.BOOK_STORY

                # 응답 메시지 저장
                await self.save_chat_message(
                    user_id,
                    "model",
                    response_text,
                    message_type
                )

            return {
                "type": "chat",
                "message": response_text,
                "data": None
            }

        except Exception as e:
            logger.error(f"Query processing error: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Query processing failed: {str(e)}"
            )

    async def get_user_files(self, user_id: str):
        """사용자의 파일 목록을 조회합니다."""
        user = await self.db.users.find_one({"email": user_id})
        if not user:
            return []
        return await self.db.files.find({
            "user_id": user["_id"]
        }).to_list(length=None)