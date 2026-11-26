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


class AudioExtractionMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["extracted", "skipped"]
    reason: str | None = None
    duration_seconds: float | None = Field(default=None, gt=0)
    sample_rate: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, gt=0)
    storage_path: str | None = None

    @model_validator(mode="after")
    def validate_extraction_result(self) -> "AudioExtractionMetadata":
        details = (
            self.duration_seconds,
            self.sample_rate,
            self.channels,
            self.storage_path,
        )
        if self.status == "extracted":
            if self.reason is not None or any(detail is None for detail in details):
                raise ValueError("Extracted audio must include its metadata and path")
        elif not self.reason or any(detail is not None for detail in details):
            raise ValueError("Skipped audio must include a reason and no audio metadata")
        return self


class TranscriptSegmentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    text: str = Field(min_length=1)
    average_log_probability: float | None = None
    no_speech_probability: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_segment(self) -> "TranscriptSegmentMetadata":
        if self.end_time <= self.start_time:
            raise ValueError("A transcript segment end must follow its start")
        if not self.text.strip():
            raise ValueError("A transcript segment must contain recognized text")
        return self


class TranscriptionMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["transcribed", "no_speech", "skipped"]
    reason: str | None = None
    detected_language: str | None = None
    language_probability: float | None = Field(default=None, ge=0, le=1)
    language_override: str | None = None
    text: str
    segments: list[TranscriptSegmentMetadata]
    processing_duration_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_transcription_result(self) -> "TranscriptionMetadata":
        if self.status == "skipped":
            if (
                not self.reason
                or self.detected_language is not None
                or self.language_probability is not None
                or self.text
                or self.segments
            ):
                raise ValueError("Skipped transcription must not include transcript data")
        elif self.status == "no_speech":
            if not self.reason or self.text or self.segments:
                raise ValueError("No-speech transcription must have empty text and segments")
        elif not self.segments or not self.text.strip() or self.reason is not None:
            raise ValueError("Transcribed results must contain recognized segments")
        return self


class VideoAnalysisArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1, 2] = 1
    frames: list[ExtractedFrameMetadata]
    scenes: list[SceneMetadata]
    summary: ProcessingSummary
    audio: AudioExtractionMetadata | None = None
    transcription: TranscriptionMetadata | None = None

    @model_validator(mode="after")
    def validate_summary_counts(self) -> "VideoAnalysisArtifact":
        if self.summary.frame_count != len(self.frames):
            raise ValueError("Frame count must match the frame metadata")
        if self.summary.scene_count != len(self.scenes):
            raise ValueError("Scene count must match the scene metadata")
        if self.schema_version == 2 and (
            self.audio is None or self.transcription is None
        ):
            raise ValueError("Version 2 artifacts must include audio processing metadata")
        return self
