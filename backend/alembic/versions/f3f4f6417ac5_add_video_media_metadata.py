"""add video media metadata

Revision ID: f3f4f6417ac5
Revises: 009dcf5dec6b
Create Date: 2026-11-24 22:07:03.325733
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa



revision: str = 'f3f4f6417ac5'
down_revision: Union[str, Sequence[str], None] = '009dcf5dec6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "videos",
        sa.Column("video_codec", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "videos",
        sa.Column("has_audio", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("videos", "has_audio")
    op.drop_column("videos", "video_codec")
