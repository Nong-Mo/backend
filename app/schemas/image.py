from pydantic import BaseModel

class ImageUploadRequest(BaseModel):
    title: str

class ImageUploadResponse(BaseModel):
    file_id: str
    message: str