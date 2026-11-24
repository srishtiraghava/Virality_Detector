from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import VideoStatus


class VideoCreate(BaseModel):
    original_filename: str = Field(min_length=1, max_length=255)
    storage_key: str = Field(min_length=1, max_length=1024)
    file_size: int = Field(gt=0)
    duration: float | None = Field(default=None, gt=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    fps: float | None = Field(default=None, gt=0)
    aspect_ratio: float | None = Field(default=None, gt=0)
    target_platform: str = Field(min_length=1, max_length=100)
    content_category: str = Field(min_length=1, max_length=100)


class VideoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    original_filename: str
    storage_key: str
    file_size: int
    duration: float | None
    width: int | None
    height: int | None
    fps: float | None
    aspect_ratio: float | None
    target_platform: str
    content_category: str
    status: VideoStatus
    created_at: datetime
    updated_at: datetime
