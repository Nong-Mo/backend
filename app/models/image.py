from pydantic import BaseModel, Field
from bson import ObjectId
from typing import List


class PyObjectId(ObjectId):
    """MongoDB ObjectId를 직렬화하기 위한 커스텀 클래스"""
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return ObjectId(v)

    @classmethod
    def __get_pydantic_json_schema__(cls, field_schema):
        field_schema.update(type="string")
        return field_schema


class ImageMetadata(BaseModel):
    filename: str
    content_type: str
    size: int


class ImageDocument(BaseModel):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    title: str
    file_id: str
    processed_files: List[ImageMetadata]
    created_at: str

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}
