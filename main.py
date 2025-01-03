from fastapi import FastAPI
from app.routes import auth, image
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AtoD")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://6c68-118-34-210-44.ngrok-free.app"],
    allow_credentials=True,
    allow_methods=["*"],  # 모든 HTTP 메서드 허용 (POST, GET, OPTIONS 등)
    allow_headers=["*"],  # 모든 HTTP 헤더 허용
)

# 인증 관련 라우트 등록
app.include_router(auth.router, prefix="/auth", tags=["auth"])

# 이미지 관련 라우트 등록
app.include_router(image.router)