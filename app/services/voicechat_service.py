import boto3
import os
import uuid
from app.core.config import AWS_VOICE_CHAT_ACCESS_KEY, AWS_VOICE_CHAT_SECRET_ACCESS_KEY, S3_REGION_NAME

def get_aws_client(service_name: str):
    # AWS 클라이언트 초기화
    return boto3.client(
        service_name,
        region_name=S3_REGION_NAME,
        aws_access_key_id=AWS_VOICE_CHAT_ACCESS_KEY,
        aws_secret_access_key=AWS_VOICE_CHAT_SECRET_ACCESS_KEY
    )

# def transcribe_audio(audio_file_path: str):
#     client = get_aws_client("transcribe")
