class VideoProcessingError(RuntimeError):
    pass


class FrameExtractionError(VideoProcessingError):
    pass


class SceneDetectionError(VideoProcessingError):
    pass


class AnalysisArtifactError(VideoProcessingError):
    pass


class AudioExtractionError(VideoProcessingError):
    pass


class TranscriptionError(VideoProcessingError):
    pass


class VoiceActivityDetectionError(VideoProcessingError):
    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
