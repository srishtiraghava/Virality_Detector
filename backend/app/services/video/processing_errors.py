class VideoProcessingError(RuntimeError):
    pass


class FrameExtractionError(VideoProcessingError):
    pass


class SceneDetectionError(VideoProcessingError):
    pass


class AnalysisArtifactError(VideoProcessingError):
    pass
