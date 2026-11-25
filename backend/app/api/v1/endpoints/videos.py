from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.config import settings
from app.database.session import get_db
from app.models.enums import VideoStatus
from app.models.user import User
from app.models.video import Video
from app.repositories.videos import get_video_by_id, list_videos
from app.schemas.analysis_job import AnalysisJobResponse
from app.schemas.video import VideoResponse
from app.services.upload.service import (
    EmptyVideoError,
    InvalidVideoError,
    UnsupportedVideoError,
    VideoTooLargeError,
    upload_video,
)
from app.services.upload.storage import (
    StorageBackend,
    StorageError,
    get_storage_backend,
)
from app.services.video.metadata import MediaToolUnavailable
from app.services.video.service import delete_owned_video
from app.services.analysis_queue import AnalysisDispatchError, enqueue_analysis_job

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
VideoStorage = Annotated[StorageBackend, Depends(get_storage_backend)]


@router.post(
    "/upload",
    response_model=VideoResponse,
    status_code=201,
)
def upload_video_endpoint(
    file: Annotated[UploadFile, File()],
    target_platform: Annotated[str, Form(min_length=1, max_length=100)],
    content_category: Annotated[str, Form(min_length=1, max_length=100)],
    session: DatabaseSession,
    current_user: CurrentUser,
    storage: VideoStorage,
) -> Video:
    try:
        return upload_video(
            session,
            current_user.id,
            file.file,
            original_filename=file.filename,
            content_type=file.content_type,
            target_platform=target_platform,
            content_category=content_category,
            max_size_bytes=settings.max_upload_size_mb * 1024 * 1024,
            storage=storage,
        )
    except UnsupportedVideoError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from None
    except EmptyVideoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except VideoTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from None
    except InvalidVideoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except MediaToolUnavailable:
        raise HTTPException(
            status_code=503,
            detail="Media inspection is unavailable",
        ) from None
    except StorageError:
        raise HTTPException(
            status_code=503,
            detail="Video storage is unavailable",
        ) from None
    finally:
        file.file.close()


@router.get("", response_model=list[VideoResponse])
def read_videos(
    session: DatabaseSession,
    current_user: CurrentUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> list[Video]:
    return list_videos(session, current_user.id, offset=offset, limit=limit)


@router.get("/{video_id}", response_model=VideoResponse)
def read_video(
    video_id: UUID,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> Video:
    video = get_video_by_id(session, current_user.id, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


@router.delete("/{video_id}", status_code=204)
def remove_video(
    video_id: UUID,
    session: DatabaseSession,
    current_user: CurrentUser,
    storage: VideoStorage,
) -> Response:
    try:
        deleted = delete_owned_video(
            session,
            current_user.id,
            video_id,
            storage,
        )
    except StorageError:
        raise HTTPException(
            status_code=503,
            detail="Video storage is unavailable",
        ) from None
    if not deleted:
        raise HTTPException(status_code=404, detail="Video not found")
    return Response(status_code=204)


@router.post(
    "/{video_id}/analyze",
    response_model=AnalysisJobResponse,
    status_code=201,
)
def create_video_analysis_job(
    video_id: UUID,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> AnalysisJobResponse:
    video = get_video_by_id(session, current_user.id, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status != VideoStatus.AVAILABLE:
        raise HTTPException(
            status_code=409,
            detail="Video is not available for analysis",
        )
    try:
        job = enqueue_analysis_job(session, video.id)
    except AnalysisDispatchError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Analysis job could not be dispatched",
                "job_id": str(exc.job_id),
            },
        ) from None
    return AnalysisJobResponse.model_validate(job)
