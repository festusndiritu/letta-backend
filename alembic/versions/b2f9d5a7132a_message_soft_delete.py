"""message soft delete

Revision ID: b2f9d5a7132a
Revises: a1c0f4d8b901
Create Date: 2026-04-09 00:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2f9d5a7132a"
down_revision: Union[str, None] = "a1c0f4d8b901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "deleted_at")

