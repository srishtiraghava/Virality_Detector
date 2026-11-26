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
