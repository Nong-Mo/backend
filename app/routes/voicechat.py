from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from app.services.voicechat_service import VoiceChatService
from app.services.image_services import verify_jwt
from app.schemas.voicechat import VoiceChatResponse
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.database import get_database

router = APIRouter()

async def get_voicechat_service(db: AsyncIOMotorClient = Depends(get_database)):
    return VoiceChatService(db)

@router.post("/chat/voice", response_model=VoiceChatResponse)
async def process_voice_chat(
    audio_file: UploadFile = File(...),
    session_id: str = None,
    user_id: str = Depends(verify_jwt),
    service: VoiceChatService = Depends(get_voicechat_service)
):
    """
    음성 파일을 받아서 AI와 대화를 처리하고 음성 응답을 반환합니다.
    """
    try:
        result = await service.process_voice_chat(
            user_id=user_id,
            audio_file=audio_file,
            session_id=session_id
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) 