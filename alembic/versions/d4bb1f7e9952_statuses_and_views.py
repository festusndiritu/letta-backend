"""statuses and status views

Revision ID: d4bb1f7e9952
Revises: c3aa0e6c8841
Create Date: 2026-04-09 00:03:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d4bb1f7e9952"
down_revision: Union[str, None] = "c3aa0e6c8841"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "statuses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("media_url", sa.Text(), nullable=True),
        sa.Column("bg_color", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("type IN ('text', 'image', 'video')", name="statuses_type_check"),
    )
    op.create_index("ix_statuses_user_id", "statuses", ["user_id"], unique=False)
    op.create_index("ix_statuses_expires", "statuses", ["expires_at"], unique=False)

    op.create_table(
        "status_views",
        sa.Column("status_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("viewer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["status_id"], ["statuses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["viewer_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("status_id", "viewer_id"),
    )


def downgrade() -> None:
    op.drop_table("status_views")
    op.drop_index("ix_statuses_expires", table_name="statuses")
    op.drop_index("ix_statuses_user_id", table_name="statuses")
    op.drop_table("statuses")

