from enum import Enum


class AnalysisJobStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class VideoStatus(str, Enum):
    PENDING = "pending"
    AVAILABLE = "available"
    FAILED = "failed"
