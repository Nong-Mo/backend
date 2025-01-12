import json
import uuid
import logging
from fastapi import HTTPException
import datetime

from app.core.exceptions import DataParsingError
from app.utils.query_util import QueryProcessor
from app.utils.tts_util import TTSUtil
from app.utils.pdf_util import PDFUtil

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self, mongodb_client):
        self.db = mongodb_client
        self.files_collection = self.db.files
        self.users_collection = self.db.users
        self.chat_collection = self.db.chat_history
        self.storage_collection = self.db.storages
        self.query_processor = QueryProcessor(mongodb_client, self.chat_collection)
        self.tts_util = TTSUtil()
        self.pdf_util = PDFUtil(mongodb_client)

    async def process_query(self, user_id: str, query: str, save_to_history: bool = True):
        """사용자 질의를 처리합니다."""
        try:
            response = await self.query_processor.process_query(
                user_id=user_id,
                query=query,
                new_chat=False,
                save_to_history=save_to_history
            )
            return response
        except Exception as e:
            logger.error(f"Query processing error: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Query processing failed: {str(e)}"
            )

    async def start_new_chat(self, user_id: str):
        """새로운 채팅 세션을 시작합니다."""
        try:
            delete_result = await self.chat_collection.delete_many({"user_id": user_id})
            return {
                "status": "success",
                "message": "New chat session started",
                "deleted_messages": delete_result.deleted_count
            }
        except Exception as e:
            logger.error(f"Error starting new chat: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start new chat: {str(e)}"
            )

    async def save_story(self, user_email: str, storage_name: str, title: str):
        """보관함 타입에 따라 다른 저장 로직을 수행합니다."""
        try:
            if storage_name == "책":
                return await self._save_book_story(user_email, title)
            elif storage_name == "영수증":
                return await self._save_receipt_analysis(user_email, title)
            else:
                return await self._save_default_story(user_email, storage_name, title)
        except Exception as e:
            logger.error(f"Error saving story: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to save story: {str(e)}"
            )

    async def _save_book_story(self, user_email: str, title: str):
        """책 보관함용 저장 로직: MP3와 PDF 생성"""
        try:
            user = await self.users_collection.find_one({"email": user_email})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            storage = await self.storage_collection.find_one({
                "user_id": user["_id"],
                "name": "책"
            })

            if not storage:
                raise HTTPException(status_code=404, detail="Storage '책' not found")

            # 마지막 LLM 응답 찾기
            last_llm_message = await self.chat_collection.find_one(
                {"user_id": user_email, "role": "model"},
                sort=[("timestamp", -1)]
            )

            if not last_llm_message:
                raise HTTPException(status_code=404, detail="No story content found")

            story_content = last_llm_message.get("content", "")
            if not story_content:
                raise HTTPException(status_code=400, detail="Invalid story content")

            # UUID 문자열로 생성
            file_id = str(uuid.uuid4())

            # 현재 시간 설정
            now = datetime.datetime.now(datetime.UTC)

            try:
                # 1. Storage count 증가 (MP3와 PDF 2개 파일)
                await self.storage_collection.update_one(
                    {"_id": storage["_id"]},
                    {
                        "$inc": {"file_count": 1},
                        "$set": {"updated_at": now}
                    }
                )

                # 2. TTS로 MP3 생성
                audio_s3_key = await self.tts_util.convert_text_to_speech(
                    story_content,
                    f"story_{file_id}",
                    title
                )

                # 3. PDF 생성
                pdf_result = await self.pdf_util.create_text_pdf(
                    user_id=user["_id"],
                    storage_id=storage["_id"],
                    content=story_content,
                    title=title
                )

                # 4. MP3 파일 메타데이터 저장
                mp3_doc = {
                    "storage_id": storage["_id"],
                    "user_id": user["_id"],
                    "title": title,
                    "filename": f"{title}.mp3",
                    "s3_key": audio_s3_key,
                    "contents": story_content,
                    "file_size": len(story_content.encode('utf-8')),
                    "mime_type": "audio/mp3",
                    "created_at": now,
                    "updated_at": now,
                    "is_primary": True
                }

                mp3_result = await self.files_collection.insert_one(mp3_doc)

                # 5. PDF 파일을 MP3와 연결하여 저장
                pdf_doc = {
                    "storage_id": storage["_id"],
                    "user_id": user["_id"],
                    "title": title,
                    "filename": f"{title}.pdf",
                    "s3_key": pdf_result["s3_key"],
                    "contents": story_content,
                    "file_size": pdf_result["file_size"],
                    "mime_type": "application/pdf",
                    "created_at": now,
                    "updated_at": now,
                    "is_primary": False,
                    "primary_file_id": mp3_result.inserted_id
                }

                await self.files_collection.insert_one(pdf_doc)

                return str(mp3_result.inserted_id)

            except Exception as e:
                # 에러 발생 시 storage count 롤백
                await self.storage_collection.update_one(
                    {"_id": storage["_id"]},
                    {
                        "$inc": {"file_count": -1},
                        "$set": {"updated_at": now}
                    }
                )
                raise e

        except Exception as e:
            logger.error(f"Error saving book story: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to save book story: {str(e)}"
            )

    def _parse_receipt_data(self, content: str) -> dict:
        """
        영수증 분석 결과에서 구조화된 데이터를 추출합니다.

        Args:
            content: LLM 응답 내용

        Returns:
            dict: 구조화된 영수증 데이터
        """
        try:
            # JSON 형식으로 저장된 OCR 결과 확인
            try:
                data = json.loads(content)
                if isinstance(data, list) and len(data) > 0:
                    # OCR 결과가 리스트 형태로 저장된 경우
                    receipt_data = {
                        "amounts": {},
                        "metadata": {}
                    }

                    # 각 영수증의 데이터 병합
                    for receipt in data:
                        if "totalPrice" in receipt:
                            receipt_data["amounts"]["총액"] = receipt["totalPrice"]
                        if "storeInfo" in receipt:
                            receipt_data["metadata"]["상점정보"] = receipt["storeInfo"]
                        if "date" in receipt:
                            receipt_data["metadata"]["날짜"] = receipt["date"]

                    return receipt_data
            except json.JSONDecodeError:
                pass

            # 텍스트 형식으로 저장된 결과 파싱
            import re
            receipt_data = {
                "amounts": {},
                "metadata": {}
            }

            # 금액 패턴 매칭
            amount_pattern = r'([가-힣\s]+)[\s:]*([\d,]+)원'
            matches = re.findall(amount_pattern, content)

            for label, amount in matches:
                label = label.strip()
                amount = int(amount.replace(',', ''))
                receipt_data["amounts"][label] = amount

            return receipt_data

        except Exception as e:
            logger.error(f"영수증 데이터 파싱 실패: {str(e)}")
            raise DataParsingError(f"영수증 데이터 파싱에 실패했습니다: {str(e)}")

    async def _save_receipt_analysis(self, user_email: str, title: str):
        """영수증 보관함용 저장 로직: 분석 결과와 시각화된 PDF 생성"""
        try:
            user = await self.users_collection.find_one({"email": user_email})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            storage = await self.storage_collection.find_one({
                "user_id": user["_id"],
                "name": "영수증"
            })

            if not storage:
                raise HTTPException(status_code=404, detail="Storage '영수증' not found")

            # 채팅 기록에서 OCR 결과와 분석 결과 찾기
            chat_history = await self.chat_collection.find({
                "user_id": user_email
            }).sort("timestamp", -1).limit(10).to_list(None)

            ocr_result = None
            analysis_content = None

            for message in chat_history:
                if message["role"] == "model":
                    analysis_content = message["content"]
                    break
                elif message["role"] == "user" and isinstance(message.get("content"), dict):
                    ocr_result = message["content"]

            if not analysis_content:
                raise HTTPException(status_code=404, detail="No analysis content found")

            # 현재 시간 설정
            now = datetime.datetime.now(datetime.UTC)

            try:
                # 1. Storage count 증가 (PDF 1개 파일)
                await self.storage_collection.update_one(
                    {"_id": storage["_id"]},
                    {
                        "$inc": {"file_count": 1},
                        "$set": {"updated_at": now}
                    }
                )

                # 2. OCR 결과와 분석 결과 파싱
                structured_data = self._parse_receipt_data(analysis_content)
                if ocr_result:
                    structured_data["ocr_result"] = ocr_result

                # 3. PDF 생성
                pdf_result = await self.pdf_util.create_analysis_pdf(
                    user_id=user["_id"],
                    storage_id=storage["_id"],
                    content=analysis_content,
                    structured_data=structured_data,
                    title=title
                )

                # 4. 파일 정보 저장
                file_doc = {
                    "storage_id": storage["_id"],
                    "user_id": user["_id"],
                    "title": title,
                    "filename": f"{title}.pdf",
                    "s3_key": pdf_result["s3_key"],
                    "contents": {
                        "text": analysis_content,
                        "structured_data": structured_data
                    },
                    "file_size": pdf_result["file_size"],
                    "mime_type": "application/pdf",
                    "created_at": now,
                    "updated_at": now,
                    "is_primary": True
                }

                result = await self.files_collection.insert_one(file_doc)
                return str(result.inserted_id)

            except Exception as e:
                # 에러 발생 시 storage count 롤백
                await self.storage_collection.update_one(
                    {"_id": storage["_id"]},
                    {
                        "$inc": {"file_count": -1},
                        "$set": {"updated_at": now}
                    }
                )
                raise e

        except Exception as e:
            logger.error(f"영수증 분석 저장 실패: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to save receipt analysis: {str(e)}"
            )

    async def _save_default_story(self, user_email: str, storage_name: str, title: str):
        """기본 저장 로직 - 텍스트 파일로 저장"""
        try:
            user = await self.users_collection.find_one({"email": user_email})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # 스토리지 찾기
            storage = await self.storage_collection.find_one({
                "user_id": user["_id"],
                "name": storage_name
            })

            if not storage:
                raise HTTPException(status_code=404, detail=f"Storage '{storage_name}' not found")

            # 마지막 LLM 응답 찾기
            last_llm_message = await self.chat_collection.find_one(
                {"user_id": user_email, "role": "model"},
                sort=[("timestamp", -1)]
            )

            if not last_llm_message:
                raise HTTPException(status_code=404, detail="No content found")

            content = last_llm_message.get("content", "")
            if not content:
                raise HTTPException(status_code=400, detail="Invalid content")

            # 현재 시간 설정
            now = datetime.datetime.now(datetime.UTC)

            try:
                # 1. Storage count 증가 (텍스트 파일 1개)
                await self.storage_collection.update_one(
                    {"_id": storage["_id"]},
                    {
                        "$inc": {"file_count": 1},
                        "$set": {"updated_at": now}
                    }
                )

                file_id = str(uuid.uuid4())
                filename = f"{title}.txt"
                s3_key = f"documents/{user_email}/{file_id}/{filename}"

                # 2. 파일 메타데이터 저장
                file_doc = {
                    "storage_id": storage["_id"],
                    "user_id": user["_id"],
                    "title": title,
                    "filename": filename,
                    "s3_key": s3_key,
                    "contents": content,
                    "file_size": len(content.encode('utf-8')),
                    "mime_type": "text/plain",
                    "created_at": now,
                    "updated_at": now,
                    "is_primary": True
                }

                result = await self.files_collection.insert_one(file_doc)
                return str(result.inserted_id)

            except Exception as e:
                # 에러 발생 시 storage count 롤백
                await self.storage_collection.update_one(
                    {"_id": storage["_id"]},
                    {
                        "$inc": {"file_count": -1},
                        "$set": {"updated_at": now}
                    }
                )
                raise e

        except Exception as e:
            logger.error(f"Error saving content: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to save content: {str(e)}"
            )
