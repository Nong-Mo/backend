from fastapi import FastAPI, Request
from app.routes import auth, image, storage, llm
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AtoD")

origins = [
    "http://localhost:3000",
    "https://e336-175-195-226-193.ngrok-free.app",
    "http://ec2-54-180-149-98.ap-northeast-2.compute.amazonaws.com",
    "https://nongmo-a2d.com",
    "http://192.168.0.117:3000",
    "https://192.168.0.117:3000"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # * 제거
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=[
        "Content-Type", 
        "Authorization", 
        "token", 
        "accept", 
        "X-Requested-With",
        "Origin",
        "Access-Control-Request-Method",
        "Access-Control-Request-Headers",
        "Content-Transfer-Encoding",
        "Content-Length",
        "Accept-Encoding",
        "Host",
        "User-Agent"
    ],
    expose_headers=["*"],
    max_age=3600,
)

# OPTIONS 요청을 명시적으로 처리하는 미들웨어 추가
@app.middleware("http")
async def options_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        response = await call_next(request)
        response.headers["Access-Control-Max-Age"] = "3600"
        return response
    response = await call_next(request)
    return response

@app.get("/health")
async def health_check():
    return {"message": "OK"}

# 라우터 등록
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(image.router)
app.include_router(storage.router, prefix="/storage", tags=["storage"])
app.include_router(llm.router, prefix="/llm", tags=["llm"])