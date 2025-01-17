from fastapi import FastAPI
from app.routes import auth, image, storage, llm
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AtoD")

# 허용할 origin 목록
origins = [
    "http://localhost:3000",
    "https://e336-175-195-226-193.ngrok-free.app",
    "http://ec2-54-180-149-98.ap-northeast-2.compute.amazonaws.com",
    "https://nongmo-a2d.com",
    "http://192.168.0.117:3000"  # 개발 환경 IP
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],  # 명시적으로 메소드 나열
    allow_headers=["Content-Type", "Authorization", "token", "accept", "X-Requested-With"],  # 필요한 헤더만 명시
    expose_headers=["*"],
    max_age=3600,  # preflight 요청 캐시 시간 (초)
)

@app.get("/health")
async def health_check():
    return {"message": "OK"}

# 인증 관련 라우트 등록
app.include_router(auth.router, prefix="/auth", tags=["auth"])

# 이미지 관련 라우트 등록
app.include_router(image.router)

# 보관함 관련 라우트 등록
app.include_router(storage.router, prefix="/storage", tags=["storage"])

# llm 관련 라우트 등록
app.include_router(llm.router, prefix="/llm", tags=["llm"])