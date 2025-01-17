from fastapi import FastAPI
from app.routes import auth, image, storage, llm
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AtoD")

# 실제 사용하는 origin만 명시
origins = [
        "http://192.168.0.117:3000",
        "https://192.168.0.117:3000",
        "https://e336-175-195-226-193.ngrok-free.app",
        "http://ec2-54-180-149-98.ap-northeast-2.compute.amazonaws.com",
        "https://nongmo-a2d.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # * 대신 실제 origin 리스트 사용
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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