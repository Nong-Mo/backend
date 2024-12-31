from pydantic import BaseModel
from typing import List


class ImageUploadRequest(BaseModel):
    title: str


class ImageUploadResponse(BaseModel):
    file_id: str
    message: str