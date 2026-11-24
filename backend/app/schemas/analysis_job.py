from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import AnalysisJobStatus


class AnalysisJobCreate(BaseModel):
    video_id: UUID


class AnalysisJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    video_id: UUID
    status: AnalysisJobStatus
    progress: int
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AnalysisJobStatusUpdate(BaseModel):
    status: AnalysisJobStatus | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    error_message: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_an_update(self) -> "AnalysisJobStatusUpdate":
        if self.status is None and self.progress is None and self.error_message is None:
            raise ValueError("At least one job field must be provided")
        return self
