from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.enums import VideoStatus
from app.models.user import utc_now


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (
        CheckConstraint("file_size > 0", name="file_size_positive"),
        CheckConstraint("duration IS NULL OR duration > 0", name="duration_positive"),
        CheckConstraint("width IS NULL OR width > 0", name="width_positive"),
        CheckConstraint("height IS NULL OR height > 0", name="height_positive"),
        CheckConstraint("fps IS NULL OR fps > 0", name="fps_positive"),
        CheckConstraint("aspect_ratio IS NULL OR aspect_ratio > 0", name="aspect_ratio_positive"),
        Index("ix_videos_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    duration: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    aspect_ratio: Mapped[float | None] = mapped_column(Float)
    target_platform: Mapped[str] = mapped_column(String(100), nullable=False)
    content_category: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[VideoStatus] = mapped_column(
        SqlEnum(
            VideoStatus,
            name="video_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        default=VideoStatus.PENDING,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="videos")
    analysis_jobs: Mapped[list["AnalysisJob"]] = relationship(
        back_populates="video",
        cascade="all, delete-orphan",
        order_by="AnalysisJob.created_at",
    )
