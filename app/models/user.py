from pydantic import BaseModel, EmailStr

class UserCreate(BaseModel):
    email: EmailStr
    nickname: str
    password: str
    password_confirmation: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str