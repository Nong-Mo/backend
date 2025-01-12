# app/routes/llm.py
from fastapi import APIRouter, Depends, Body
from app.services.llm_service import LLMService
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.database import get_database
from app.utils.auth_util import verify_jwt
from pydantic import BaseModel, Field
from typing import Optional, Dict

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

@router.post("/save-story", response_model=dict)
async def save_story(
       request: SaveStoryRequest,
       user_id: str = Depends(verify_jwt),
       llm_service: LLMService = Depends(get_llm_service)
):
   """
   마지막 LLM 응답을 단편소설로 저장합니다.

   Args:
       request: 저장할 단편소설 정보 (보관함 이름, 제목)
       user_id: JWT에서 추출한 사용자 ID
       llm_service: LLM 서비스 인스턴스

   Returns:
       dict: 저장 결과 및 파일 ID
   """
   file_id = await llm_service.save_story(
       user_id,
       request.storage_name,
       request.title
   )
   return {
       "status": "success",
       "message": "단편소설이 저장되었습니다.",
       "file_id": file_id
   }