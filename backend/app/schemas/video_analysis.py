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


class VoiceActivityIntervalMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    duration: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_interval(self) -> "VoiceActivityIntervalMetadata":
        if self.end_time <= self.start_time:
            raise ValueError("An interval end must follow its start")
        if abs(self.duration - (self.end_time - self.start_time)) > 1e-6:
            raise ValueError("Interval duration must match its time bounds")
        return self


class SpeechSegmentMetadata(VoiceActivityIntervalMetadata):
    pass


class SilenceIntervalMetadata(VoiceActivityIntervalMetadata):
    pass


class VADConfigurationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: Literal["silero-vad"] = "silero-vad"
    model_version: Literal["6.2.0"] = "6.2.0"
    device: Literal["cpu"] = "cpu"
    sample_rate: Literal[16000] = 16000
    threshold: float = Field(ge=0, le=1)
    min_speech_duration_ms: int = Field(ge=0)
    min_silence_duration_ms: int = Field(ge=0)
    speech_pad_ms: int = Field(ge=0)


class VADStatisticsMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_audio_duration_seconds: float = Field(ge=0)
    total_speech_duration_seconds: float = Field(ge=0)
    total_silence_duration_seconds: float = Field(ge=0)
    speech_to_audio_ratio: float | None = Field(default=None, ge=0, le=1)
    silence_to_audio_ratio: float | None = Field(default=None, ge=0, le=1)
    speech_segment_count: int = Field(ge=0)
    average_speech_segment_duration_seconds: float | None = Field(
        default=None,
        ge=0,
    )
    longest_silence_interval_seconds: float | None = Field(default=None, ge=0)


class VoiceActivityAnalysisMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["analyzed", "skipped", "unavailable"]
    reason: str | None = None
    configuration: VADConfigurationMetadata
    speech_segments: list[SpeechSegmentMetadata] = Field(default_factory=list)
    silence_intervals: list[SilenceIntervalMetadata] = Field(default_factory=list)
    statistics: VADStatisticsMetadata | None = None
    processing_duration_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_analysis_result(self) -> "VoiceActivityAnalysisMetadata":
        if self.status == "analyzed":
            if self.reason is not None or self.statistics is None:
                raise ValueError("Analyzed VAD results must include statistics")
            if self.statistics.total_audio_duration_seconds <= 0:
                raise ValueError("Analyzed VAD audio duration must be positive")
            if self.statistics.speech_segment_count != len(self.speech_segments):
                raise ValueError("Speech segment count must match detected segments")
            _validate_vad_intervals(
                self.speech_segments,
                self.statistics.total_audio_duration_seconds,
            )
            _validate_vad_intervals(
                self.silence_intervals,
                self.statistics.total_audio_duration_seconds,
            )
            _validate_vad_coverage(
                self.speech_segments,
                self.silence_intervals,
                self.statistics.total_audio_duration_seconds,
            )
            _validate_vad_statistics(
                self.statistics,
                self.speech_segments,
                self.silence_intervals,
            )
        elif not self.reason or self.speech_segments or self.silence_intervals:
            raise ValueError(
                "Skipped or unavailable VAD must include a reason and no intervals"
            )
        elif self.status == "unavailable" and self.statistics is not None:
            raise ValueError("Unavailable VAD cannot include statistics")
        elif self.status == "skipped" and self.reason == "empty_audio":
            if (
                self.statistics is None
                or self.statistics.total_audio_duration_seconds != 0
                or self.statistics.total_speech_duration_seconds != 0
                or self.statistics.total_silence_duration_seconds != 0
                or self.statistics.speech_to_audio_ratio is not None
                or self.statistics.silence_to_audio_ratio is not None
                or self.statistics.speech_segment_count != 0
                or self.statistics.average_speech_segment_duration_seconds is not None
                or self.statistics.longest_silence_interval_seconds is not None
            ):
                raise ValueError(
                    "Empty audio must have zero totals and undefined ratios"
                )
        elif self.status == "skipped" and self.statistics is not None:
            raise ValueError("Skipped VAD cannot include statistics")
        return self


def _validate_vad_intervals(
    intervals: list[VoiceActivityIntervalMetadata],
    audio_duration: float,
) -> None:
    previous_end = 0.0
    for interval in intervals:
        if interval.end_time > audio_duration + 1e-6:
            raise ValueError("VAD intervals must stay within the audio duration")
        if interval.start_time < previous_end - 1e-6:
            raise ValueError("VAD intervals must be sorted and non-overlapping")
        previous_end = interval.end_time


def _validate_vad_coverage(
    speech_segments: list[SpeechSegmentMetadata],
    silence_intervals: list[SilenceIntervalMetadata],
    audio_duration: float,
) -> None:
    intervals = sorted(
        [*speech_segments, *silence_intervals],
        key=lambda interval: interval.start_time,
    )
    cursor = 0.0
    for interval in intervals:
        if abs(interval.start_time - cursor) > 1e-6:
            raise ValueError("Speech and silence intervals must cover audio exactly")
        cursor = interval.end_time
    if abs(cursor - audio_duration) > 1e-6:
        raise ValueError("Speech and silence intervals must cover audio exactly")


def _validate_vad_statistics(
    statistics: VADStatisticsMetadata,
    speech_segments: list[SpeechSegmentMetadata],
    silence_intervals: list[SilenceIntervalMetadata],
) -> None:
    speech_duration = sum(segment.duration for segment in speech_segments)
    silence_duration = sum(interval.duration for interval in silence_intervals)
    audio_duration = statistics.total_audio_duration_seconds
    if abs(statistics.total_speech_duration_seconds - speech_duration) > 1e-6:
        raise ValueError("Total speech duration must match speech segments")
    if abs(statistics.total_silence_duration_seconds - silence_duration) > 1e-6:
        raise ValueError("Total silence duration must match silence intervals")
    if abs(audio_duration - speech_duration - silence_duration) > 1e-6:
        raise ValueError("Speech and silence durations must cover the audio")

    expected_speech_ratio = speech_duration / audio_duration if audio_duration else None
    expected_silence_ratio = silence_duration / audio_duration if audio_duration else None
    if statistics.speech_to_audio_ratio != expected_speech_ratio:
        if (
            statistics.speech_to_audio_ratio is None
            or expected_speech_ratio is None
            or abs(statistics.speech_to_audio_ratio - expected_speech_ratio) > 1e-6
        ):
            raise ValueError("Speech-to-audio ratio does not match durations")
    if statistics.silence_to_audio_ratio != expected_silence_ratio:
        if (
            statistics.silence_to_audio_ratio is None
            or expected_silence_ratio is None
            or abs(statistics.silence_to_audio_ratio - expected_silence_ratio) > 1e-6
        ):
            raise ValueError("Silence-to-audio ratio does not match durations")

    expected_average = speech_duration / len(speech_segments) if speech_segments else None
    expected_longest = max(
        (interval.duration for interval in silence_intervals),
        default=None,
    )
    if statistics.average_speech_segment_duration_seconds != expected_average:
        if (
            statistics.average_speech_segment_duration_seconds is None
            or expected_average is None
            or abs(
                statistics.average_speech_segment_duration_seconds
                - expected_average
            )
            > 1e-6
        ):
            raise ValueError("Average speech duration does not match segments")
    if statistics.longest_silence_interval_seconds != expected_longest:
        if (
            statistics.longest_silence_interval_seconds is None
            or expected_longest is None
            or abs(
                statistics.longest_silence_interval_seconds - expected_longest
            )
            > 1e-6
        ):
            raise ValueError("Longest silence duration does not match intervals")


class VideoAnalysisArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1, 2] = 1
    frames: list[ExtractedFrameMetadata]
    scenes: list[SceneMetadata]
    summary: ProcessingSummary
    audio: AudioExtractionMetadata | None = None
    transcription: TranscriptionMetadata | None = None
    vad: VoiceActivityAnalysisMetadata | None = None

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
