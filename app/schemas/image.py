from pydantic import BaseModel
from typing import List, Dict, Optional

class Point(BaseModel):
    x: float
    y: float

class PageVertices(BaseModel):
    points: List[Point]

class ImageUploadRequest(BaseModel):
    title: str

class ImageUploadResponse(BaseModel):
    file_id: str
    message: str