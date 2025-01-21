# A2D (Analog to Digital) Project

## Overview
A2D는 오디오 및 문서를 통합 관리하는 FastAPI 기반의 백엔드 서비스입니다. 사용자가 업로드한 이미지를 OCR로 텍스트를 추출하여 text PDF를 생성하고, TTS를 통해 오디오로 변환하여 다양한 형태로 보관할 수 있습니다.

## 기술 스택
- **Framework**: FastAPI
- **Database**: MongoDB
- **Infrastructure & Cloud**:
  - AWS EC2 (서버 호스팅)
  - AWS S3 (파일 스토리지)
  - AWS CloudFront (CDN 서비스)
- **External APIs**: 
  - Naver Clova OCR
  - Naver Clova Receipt OCR
  - Naver Cloud Platform TTS
  - Google Gemini AI

## 기술 선택 이유

### 1. Naver Clova OCR
- 한글 텍스트 인식률이 타 서비스 대비 우수
- 이미지 회전 45도까지 높은 인식률
- 무료 크래딧 10만원 지원으로 개발 및 테스트 용이

### 2. Naver Clova TTS
- 자연스러운 한국어 음성 합성 품질
- 다양한 음색과 감정 표현 지원
- 무료 크래딧 10만원 지원으로 서비스 개발 가능

### 3. AWS EC2
- 안정적인 서버 운영 환경 제공
- 자동 스케일링 및 로드 밸런싱 지원
- 다양한 인스턴스 타입으로 유연한 리소스 관리
- 크래프톤 정글 지원 100만원 크래딧으로 인프라 구축

### 4. AWS S3
- 높은 확장성과 내구성을 가진 객체 스토리지
- 이미지, PDF, 오디오 파일의 효율적인 저장 및 관리
- 정적 웹사이트 호스팅 기능 활용
- 크래프톤 정글 지원 100만원 크래딧으로 스토리지 운영

### 5. AWS CloudFront
- 전역 CDN으로 빠른 콘텐츠 전송
- PDF 및 미디어 파일의 효율적인 캐싱
- HTTPS 지원으로 안전한 콘텐츠 전송
- 크래프톤 정글 지원 100만원 크래딧으로 CDN 서비스 구축

### 6. FastAPI 선택 이유
1. **비동기 처리**: FastAPI는 비동기 처리를 기본으로 지원하며, 특히 OCR, TTS와 같은 외부 API 호출이 많은 서비스에서 높은 성능을 발휘합니다.
   ```python
   async def process_images(self, storage_name: str, title: str, files: List[UploadFile], ...)
   ```

2. **자동 API 문서화**: Swagger UI와 ReDoc을 통한 자동 API 문서화를 제공하여 프론트엔드 개발자와의 협업이 용이합니다.

3. **타입 힌팅**: Pydantic을 통한 강력한 타입 검증으로 런타임 에러를 방지할 수 있습니다.
   ```python
   class UserCreate(BaseModel):
       email: EmailStr
       nickname: str
       password: str
   ```

4. **고성능**: ASGI 서버를 사용하여 높은 처리량과 낮은 지연시간을 제공합니다.

## API 설계

### 1. Auth Routes (/auth)
- POST /auth/signup: 새로운 사용자 등록
- POST /auth/signin: 사용자 로그인

### 2. Image Routes (/images)
- POST /images/upload: 이미지 업로드 및 OCR 처리
- POST /receipt/ocr: 영수증 이미지 OCR 처리

### 3. Storage Routes (/storage)
- GET /storage/list: 전체 보관함 목록 조회
- GET /storage/{storage_name}: 특정 보관함 상세 조회
- GET /storage/files/{file_id}: 특정 파일 상세 조회
- DELETE /storage/files/{file_id}: 파일 삭제
- POST /storage/convert-to-pdf: 이미지를 PDF로 변환

### 4. LLM Routes (/llm)
- POST /llm/query: LLM 질의 처리
- POST /llm/new-chat: 새로운 채팅 세션 시작
- POST /llm/save-story: 스토리 저장

## ERD 설계

### Users Collection
```
{
  _id: ObjectId,          // MongoDB 기본 ID
  email: String,          // 사용자 이메일
  nickname: String,       // 최대 8자 제한
  password: String,       // bcrypt 해시 처리
  created_at: DateTime,   // 생성 시간
  updated_at: DateTime    // 수정 시간
}
```

### Storages Collection
```
{
  _id: ObjectId,
  user_id: ObjectId,      // Users 컬렉션 참조
  name: String,           // 보관함 이름 (영감, 소설 등)
  file_count: Integer,    // 파일 수 캐싱
  created_at: DateTime,
  updated_at: DateTime
}
```

### Files Collection
```
{
  _id: ObjectId,
  storage_id: ObjectId,   // Storages 컬렉션 참조
  user_id: ObjectId,      // Users 컬렉션 참조
  title: String,          // 파일 제목
  filename: String,       // 실제 파일명
  s3_key: String,        // S3 저장 경로
  contents: Mixed,        // 텍스트 또는 구조화된 데이터
  file_size: Integer,     // 파일 크기 (bytes)
  mime_type: String,      // 파일 타입
  created_at: DateTime,
  updated_at: DateTime,
  is_primary: Boolean,    // 주 파일 여부
  primary_file_id: ObjectId  // 관련 주 파일 참조
}
```

### Chat History Collection
```
{
  _id: ObjectId,
  user_id: String,        // 사용자 식별자
  role: String,           // 대화 역할 (user/model)
  content: Mixed,         // 대화 내용
  message_type: String,   // 메시지 타입
  timestamp: DateTime     // 대화 시간
}
```
### 주요 설계 고려사항

1. **ObjectId 사용**

- 분산 환경에서 ID 충돌 방지
- 시간 정보가 포함된 ID로 생성 시점 추적 가능


2. **중복 데이터 허용**

- Files 컬렉션의 user_id: 빈번한 사용자별 파일 조회 최적화
- Storages 컬렉션의 file_count: 보관함 목록 조회 성능 개선

## 주요 트러블 슈팅

### 1. FastAPI UploadFile 파일 읽기 문제

### 문제 개요
Clova OCR API 호출 시 업로드된 이미지 파일을 읽을 때, 파일이 제대로 읽히지 않는 문제가 발생했습니다. 파일 크기는 정상이나 `file.read()`가 빈 바이트 문자열(`b''`)을 반환했습니다.

### 문제 발생 원인
`UploadFile` 객체의 파일 포인터가 이미 파일 끝에 위치해 있어 추가 읽기가 불가능한 상태였습니다.

### 해결 과정

1. **파일 포인터 위치 확인**
```python
# 기존 코드
async def _call_clova_ocr(self, file: UploadFile):
    try:
        contents = await file.read()  # 빈 내용 반환
```

2. **파일 포인터 초기화 적용**
```python
# 수정된 코드
async def _call_clova_ocr(self, file: UploadFile):
    try:
        file.file.seek(0)  # 파일 포인터 초기화
        contents = await file.read()  # 정상적으로 내용 읽기
```

### 해결 결과
- `seek(0)` 호출로 파일 포인터를 초기화하여 파일 내용을 정상적으로 읽을 수 있게 되었습니다.
- OCR 처리가 정상적으로 진행되어 텍스트 추출이 가능해졌습니다.

### 2. 이미지 변환 최적화: Perspective Transform 적용

### 문제 개요
OCR 처리 시 이미지가 기울어져 있거나 일정한 각도로 촬영된 경우, 텍스트 인식률이 저하되는 문제가 발생했습니다.

### 해결 방안
사용자로부터 문서의 4개 모서리 좌표를 입력받아 Perspective Transform을 적용하여 이미지를 보정하는 방식을 도입했습니다.

```python
async def transform_image(self, image_bytes: bytes, vertices: List[Dict[str, float]]) -> bytes:
    # 4개의 좌표점 검증
    if len(vertices) != 4:
        raise HTTPException(status_code=400, detail="정확히 4개의 좌표가 필요합니다")

    # 이미지를 numpy 배열로 변환
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    # 소스 좌표 설정
    src_points = np.float32([[v["x"], v["y"]] for v in vertices])

    # 목적지 좌표 계산 (직사각형 형태로 변환)
    width = max(
        np.linalg.norm(src_points[1] - src_points[0]),
        np.linalg.norm(src_points[2] - src_points[3])
    )
    height = max(
        np.linalg.norm(src_points[3] - src_points[0]),
        np.linalg.norm(src_points[2] - src_points[1])
    )

    # 변환 행렬 계산 및 적용
    matrix = cv2.getPerspectiveTransform(src_points, dst_points)
    transformed = cv2.warpPerspective(img, matrix, (int(width), int(height)))
```

### 해결 결과
- 기울어진 이미지의 텍스트 인식률 1% 향상
- 다양한 각도에서 촬영된 이미지 처리 가능

### 3. LLM 프롬프트 분기 처리 최적화

### 문제 개요
사용자의 다양한 요청(검색, 분석, 스토리 생성 등)에 따라 적절한 프롬프트를 생성하고 처리해야 하는 복잡성이 증가했습니다.

### 해결 방안
의도 분류(Intent Classification) 시스템을 구현하여 사용자 요청을 체계적으로 분류하고 처리하도록 설계했습니다.

```python
def classify_intention_once(self, user_query: str) -> str:
    # 검색 의도 확인
    search_keywords = ["찾아", "검색", "알려줘"]
    if any(keyword in user_query for keyword in search_keywords):
        subject = extract_main_subject(user_query)
        return f"SEARCH:{subject}"

    # 분석 의도 확인
    operations = {
        "분석": "ANALYSIS",
        "요약": "SUMMARY",
        "서평": "REVIEW",
        "블로그": "BLOG"
    }
    
    # 스토리 생성 의도 확인
    story_keywords = ["스토리", "이야기", "소설"]
    if any(keyword in user_query for keyword in story_keywords):
        return "STORY"
```

### 분기 처리 예시
1. **검색 요청**: "영수증 내역 찾아줘" → SEARCH 프롬프트 생성
2. **분석 요청**: "이 글 분석해줘" → ANALYSIS 프롬프트 생성
3. **스토리 생성**: "단편소설 써줘" → STORY 프롬프트 생성

### 해결 결과
- 사용자 의도에 따른 정확한 응답 제공
- 프롬프트 재사용성 향상
- 유지보수 용이성 개선
  
## 실행 방법

1. 환경 변수 설정
```bash
cp .env.example .env
# .env 파일에 필요한 환경 변수 설정
```

2. 의존성 설치
```bash
pip install -r requirements.txt
```

3. 서버 실행
```bash
uvicorn app.main:app --reload
```

## API 문서
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
