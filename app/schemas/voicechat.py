from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class ChatMessage(BaseModel):
    role: str  # "user" 또는 "assistant"
    content: str
    timestamp: datetime

class ChatSession(BaseModel):
    session_id: str
    user_id: str
    messages: List[ChatMessage]
    created_at: datetime
    updated_at: datetime

class VoiceChatResponse(BaseModel):
    message: str
    audio_url: Optional[str] = None 