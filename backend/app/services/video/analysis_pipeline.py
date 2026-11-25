from pathlib import Path

from app.models.analysis_job import AnalysisJob
from app.models.video import Video


class AnalysisPipelineUnavailable(RuntimeError):
    pass


def run_analysis_pipeline(
    job: AnalysisJob,
    video: Video,
    media_path: Path,
) -> None:
    raise AnalysisPipelineUnavailable(
        "Multimodal video analysis is not implemented yet"
    )
