from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import VideoStatus
from app.models.video import Video
from app.schemas.video import VideoCreate


def create_video(
    session: Session,
    user_id: UUID,
    video_data: VideoCreate,
    *,
    status: VideoStatus = VideoStatus.PENDING,
) -> Video:
    video = Video(user_id=user_id, status=status, **video_data.model_dump())
    session.add(video)
    session.commit()
    session.refresh(video)
    return video


def get_video_by_id(session: Session, user_id: UUID, video_id: UUID) -> Video | None:
    statement = select(Video).where(Video.id == video_id, Video.user_id == user_id)
    return session.scalar(statement)


def list_videos(
    session: Session,
    user_id: UUID,
    *,
    offset: int = 0,
    limit: int = 100,
) -> list[Video]:
    if offset < 0:
        raise ValueError("offset must be non-negative")
    if limit < 1:
        raise ValueError("limit must be positive")

    statement = (
        select(Video)
        .where(Video.user_id == user_id)
        .order_by(Video.created_at.desc(), Video.id)
        .offset(offset)
        .limit(limit)
    )
    return list(session.scalars(statement))


def delete_video(session: Session, user_id: UUID, video_id: UUID) -> bool:
    video = get_video_by_id(session, user_id, video_id)
    if video is None:
        return False

    session.delete(video)
    session.commit()
    return True
