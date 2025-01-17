from fastapi import FastAPI
from app.routes import auth, image, storage, llm
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AtoD")

# 허용할 origin 목록
origins = [
    "http://localhost:3000",
    "https://e336-175-195-226-193.ngrok-free.app",
    "http://ec2-54-180-149-98.ap-northeast-2.compute.amazonaws.com",
    "https://nongmo-a2d.com"
    # "*" 제거 - allow_credentials=True와 함께 사용할 수 없음
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]  # 클라이언트에서 접근 가능한 헤더 설정
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