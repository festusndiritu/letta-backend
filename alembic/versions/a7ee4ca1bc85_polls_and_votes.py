"""polls and votes

Revision ID: a7ee4ca1bc85
Revises: f6dd3b90ab74
Create Date: 2026-04-09 00:06:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a7ee4ca1bc85"
down_revision: Union[str, None] = "f6dd3b90ab74"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("poll_data", sa.Text(), nullable=True))

    op.drop_constraint("messages_type_check", "messages", type_="check")
    op.create_check_constraint(
        "messages_type_check",
        "messages",
        "type IN ('text', 'image', 'video', 'audio', 'document', 'poll')",
    )

    op.create_table(
        "poll_votes",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("option_indices", sa.Text(), nullable=False),
        sa.Column("voted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("message_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("poll_votes")

    op.drop_constraint("messages_type_check", "messages", type_="check")
    op.create_check_constraint(
        "messages_type_check",
        "messages",
        "type IN ('text', 'image', 'video', 'audio', 'document')",
    )

    op.drop_column("messages", "poll_data")

