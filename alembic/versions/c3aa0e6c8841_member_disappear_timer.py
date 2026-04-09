"""member disappear timer

Revision ID: c3aa0e6c8841
Revises: b2f9d5a7132a
Create Date: 2026-04-09 00:02:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3aa0e6c8841"
down_revision: Union[str, None] = "b2f9d5a7132a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "members",
        sa.Column(
            "disappear_after_seconds",
            sa.Integer(),
            nullable=True,
            comment="If set, messages in this conversation expire after N seconds",
        ),
    )


def downgrade() -> None:
    op.drop_column("members", "disappear_after_seconds")

