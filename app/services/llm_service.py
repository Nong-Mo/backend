import uuid
import logging
from fastapi import HTTPException
import datetime
from app.utils.query_util import QueryProcessor
from app.utils.tts_util import TTSUtil

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

    # app/services/llm_service.py
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
        """최근 대화 내용을 스토리로 저장합니다."""
        try:
            user = await self.users_collection.find_one({"email": user_email})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            storage = await self.storage_collection.find_one({
                "user_id": user["_id"],
                "name": storage_name
            })

            if not storage:
                raise HTTPException(
                    status_code=404,
                    detail=f"Storage '{storage_name}' not found"
                )

            # 마지막 LLM 응답 메시지 찾기 (role이 'model'인 메시지)
            last_llm_message = await self.chat_collection.find_one(
                {
                    "user_id": user_email,
                    "role": "model"
                },
                sort=[("timestamp", -1)]
            )

            if not last_llm_message:
                raise HTTPException(
                    status_code=404,
                    detail="No story content found"
                )

            # message 내용 확인
            story_content = last_llm_message.get("content", "")
            if not story_content:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid story content"
                )

            file_id = str(uuid.uuid4())
            filename = f"{title}.txt"
            s3_key = f"stories/{user_email}/{file_id}/{filename}"

            # TTS 변환 및 저장
            audio_s3_key = await self.tts_util.convert_text_to_speech(
                story_content,
                f"story_{file_id}",
                storage_name
            )

            # 텍스트 파일 메타데이터 저장
            file_doc = {
                "storage_id": storage["_id"],
                "user_id": user["_id"],
                "title": title,
                "filename": filename,
                "s3_key": s3_key,
                "contents": story_content,
                "file_size": len(story_content.encode('utf-8')),
                "mime_type": "text/plain",
                "created_at": datetime.datetime.now(datetime.UTC),
                "updated_at": datetime.datetime.now(datetime.UTC),
                "is_primary": False,
                "audio_s3_key": audio_s3_key
            }

            result = await self.files_collection.insert_one(file_doc)
            return str(result.inserted_id)

        except Exception as e:
            logger.error(f"Error saving story: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to save story: {str(e)}"
            )

    async def search_file(self, user_id: str, query: str):
        """파일을 검색합니다."""
        try:
            return await self.query_processor.search_file(user_id, query)
        except Exception as e:
            logger.error(f"File search error: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to search file: {str(e)}"
            )
