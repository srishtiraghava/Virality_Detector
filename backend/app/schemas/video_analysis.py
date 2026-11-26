from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExtractedFrameMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: float = Field(ge=0)
    storage_path: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class SceneMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    duration: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_scene_bounds(self) -> "SceneMetadata":
        if self.end_time <= self.start_time:
            raise ValueError("A scene end time must follow its start time")
        if abs(self.duration - (self.end_time - self.start_time)) > 1e-6:
            raise ValueError("Scene duration must match its time bounds")
        return self


class ProcessingSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frame_count: int = Field(ge=0)
    scene_count: int = Field(ge=0)
    processing_duration_seconds: float = Field(ge=0)


class VideoAnalysisArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    frames: list[ExtractedFrameMetadata]
    scenes: list[SceneMetadata]
    summary: ProcessingSummary

    @model_validator(mode="after")
    def validate_summary_counts(self) -> "VideoAnalysisArtifact":
        if self.summary.frame_count != len(self.frames):
            raise ValueError("Frame count must match the frame metadata")
        if self.summary.scene_count != len(self.scenes):
            raise ValueError("Scene count must match the scene metadata")
        return self
