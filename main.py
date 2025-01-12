from fastapi import FastAPI
from app.routes import auth, image, storage, llm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="AtoD")

origins = [
    "http://localhost:3000",
    "https://e336-175-195-226-193.ngrok-free.app",
    "http://ec2-54-180-149-98.ap-northeast-2.compute.amazonaws.com",
    "https://nongmo-a2d.com",
    "*"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
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

# Swagger 설정 추가
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title="AtoD API",  # 스웨거 문서 제목
        version="1.0.0",
        description="AtoD API 문서입니다.", # 스웨거 문서 설명
        routes=app.routes,
    )
    openapi_schema["components"]["securitySchemes"] = {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
    }
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi
app.mount("/docs", StaticFiles(directory="static"), name="static")