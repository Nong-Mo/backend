import google.generativeai as genai
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
import json
from app.core.config import GOOGLE_API_KEY
import logging

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self, mongodb_client: AsyncIOMotorClient):
        self.db = mongodb_client
        self.files_collection = self.db.files
        self.users_collection = self.db.users

        # Gemini API 설정
        genai.configure(api_key=GOOGLE_API_KEY)
        self.model = genai.GenerativeModel("gemini-1.5-flash")

    async def verify_user_access(self, user_id: str):
        user = await self.users_collection.find_one({"email": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    async def get_user_files(self, user_id: str):
        user = await self.verify_user_access(user_id)
        files = await self.files_collection.find({
            "user_id": user["_id"]
        }).to_list(length=None)
        return files

    async def process_query(self, user_id: str, query: str):
        try:
            logger.info(f"Processing query: {query} for user: {user_id}")

            files = await self.get_user_files(user_id)
            logger.debug(f"Found {len(files)} files for user")

            if not files:
                return "파일을 찾을 수 없습니다."

            context = []
            for file in files:
                try:
                    file_info = {
                        "title": file.get("title", "제목 없음"),
                        "created_at": file["created_at"].isoformat(),
                        "type": file.get("mime_type", "unknown"),
                        "content": file.get("contents", {}), # contents 가 없으면 빈 dict 반환
                    }
                    context.append(file_info)
                except KeyError as e:
                    logger.error(f"Missing required field in file {file.get('title', 'unknown')}: {e}")
                    continue
                except Exception as e:
                    logger.error(f"Error processing file {file.get('title', 'unknown')}: {e}")
                    continue

            if not context:
                return "파일 처리 중 오류가 발생했습니다."

            prompt = f"""
            다음은 사용자의 파일 정보입니다:
            {json.dumps(context, ensure_ascii=False, indent=2)}

            사용자 질문: {query}

            답변 규칙:
            1. 이동 요청 시:
            - 형식: "[보관함명] 보관함으로 이동하시려면 상단의 '[보관함명]' 버튼을 클릭해주세요."

            2. 금액 질문 시:
            - 영수증의 경우: 총액과 결제 수단 포함
            - 예시: "총 7,500원이 IBK비씨카드로 결제되었습니다."

            3. 목록 요청 시:
            - 해당 종류의 파일 수 먼저 언급
            - 최신 순으로 중요 정보만 나열
            - 영수증: 가게명, 금액
            - 책: 제목, 저자
            - 티켓: 행사명, 날짜
            - 기타: 제목, 날짜

            4. 공통 규칙:
            - 불필요한 마크다운 구문 사용 금지
            - 중복 정보 제거
            - 간단명료한 답변
            """

            logger.debug("Sending prompt to Gemini API")
            response = self.model.generate_content(prompt)

            if not response or not response.text:
                raise HTTPException(
                    status_code=500,
                    detail="LLM이 응답을 생성하지 못했습니다."
                )

            logger.info("Successfully generated response from Gemini")
            return response.text

        except Exception as e:
            logger.error(f"Error in process_query: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"LLM 처리 중 오류가 발생했습니다: {str(e)}"
            )