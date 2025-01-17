from fastapi import FastAPI
from app.routes import auth, image, storage, llm
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AtoD")

origins = [
    "http://localhost:3000",
    "https://e336-175-195-226-193.ngrok-free.app",
    "http://ec2-54-180-149-98.ap-northeast-2.compute.amazonaws.com",
    "https://nongmo-a2d.com",
    "http://192.168.0.117:3000",
    "*"  # 개발 중에는 모든 origin 허용
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=[
        "Content-Type", 
        "Authorization", 
        "token", 
        "accept", 
        "X-Requested-With",
        "Origin",
        "Access-Control-Request-Method",
        "Access-Control-Request-Headers"
    ],
    expose_headers=["*"],
    max_age=3600,
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