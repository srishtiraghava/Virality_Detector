from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.repositories.videos import delete_video, get_video_by_id
from app.services.upload.storage import StorageBackend


def delete_owned_video(
    session: Session,
    user_id: UUID,
    video_id: UUID,
    storage: StorageBackend,
) -> bool:
    video = get_video_by_id(session, user_id, video_id)
    if video is None:
        return False

    staged_deletion = storage.stage_delete(video.storage_key)
    try:
        deleted = delete_video(session, user_id, video_id)
    except SQLAlchemyError:
        session.rollback()
        staged_deletion.restore()
        raise

    if not deleted:
        staged_deletion.restore()
        return False

    staged_deletion.commit()
    return True
