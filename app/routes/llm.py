# app/routes/llm.py
from fastapi import APIRouter, Depends, Body, Query, HTTPException
from app.services.llm_service import LLMService
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.database import get_database
from app.utils.auth_util import verify_jwt
from pydantic import BaseModel, Field
from typing import Optional, Dict
from bson.objectid import ObjectId

router = APIRouter()


# Response 모델 정의
class LLMResponse(BaseModel):
   type: str
   message: str
   data: Optional[Dict] = None


class NewChatResponse(BaseModel):
   status: str
   message: str
   deleted_messages: int


# Request 모델 정의
class LLMQuery(BaseModel):
   query: str


async def get_llm_service(db: AsyncIOMotorClient = Depends(get_database)):
   return LLMService(mongodb_client=db)


# app/routes/llm.py
@router.post("/query", response_model=LLMResponse)
async def process_llm_query(
       query_data: LLMQuery = Body(...),
       user_id: str = Depends(verify_jwt),
       llm_service: LLMService = Depends(get_llm_service)
):
   """
   사용자의 질의를 처리하여 저장된 파일들에서 답변을 찾습니다.

   Args:
       query_data: LLMQuery 모델로 정의된 사용자의 질문
       user_id: JWT에서 추출한 사용자 ID
       llm_service: LLM 서비스 인스턴스

   Returns:
       LLMResponse: LLM의 응답 (type, message, data 포함)
   """
   # 일반 채팅과 파일 검색은 채팅 히스토리 저장
   response = await llm_service.process_query(
       user_id=user_id,
       query=query_data.query,
       save_to_history=True
   )
   return response


@router.post("/new-chat", response_model=NewChatResponse)
async def start_new_chat(
   user_id: str = Depends(verify_jwt),
   llm_service: LLMService = Depends(get_llm_service)
):
   """
   새로운 채팅 세션을 시작하고 이전 채팅 기록을 삭제합니다.

   Args:
       user_id: JWT에서 추출한 사용자 ID
       llm_service: LLM 서비스 인스턴스

   Returns:
       NewChatResponse: 새 채팅 시작 결과
   """
   result = await llm_service.start_new_chat(user_id)
   return NewChatResponse(**result)


class SaveStoryRequest(BaseModel):
    storage_name: str = Field(..., description="저장할 보관함 이름 (예: '책', '영수증' 등)")
    title: str = Field(..., description="단편소설 제목")
    message_id: str = Field(..., description="저장할 메시지 ID") 

@router.post("/save-story", response_model=dict)
async def save_story(
    request: SaveStoryRequest,
    user_id: str = Depends(verify_jwt),
    llm_service: LLMService = Depends(get_llm_service),
    # message_id: str = Query(..., description="저장할 메시지 ID") # message_id 쿼리 파라미터 추가
):
    """
    마지막 LLM 응답을 단편소설로 저장합니다.
    """
    try:
        if not ObjectId.is_valid(request.message_id):
            raise HTTPException(status_code=400, detail="유효하지 않은 메시지 ID입니다.")

        file_id = await llm_service.save_story(
            user_id,
            request.storage_name,
            request.title,
            request.message_id # message_id 전달
        )
        return {"status": "success", "message": "결과가 저장되었습니다.", "file_id": file_id}
    except HTTPException as http_ex: # HTTPException 처리 추가
        return {"status": "error", "message": str(http_ex.detail), "file_id": None}
    except Exception as e:
        logger.error(f"스토리 저장 중 오류 발생: {e}")
        return {"status": "error", "message": "스토리 저장 중 오류가 발생했습니다.", "file_id": None}