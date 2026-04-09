"""section9 indexes part2

Revision ID: b8ff5db2cd96
Revises: a7ee4ca1bc85
Create Date: 2026-04-09 00:07:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "b8ff5db2cd96"
down_revision: Union[str, None] = "a7ee4ca1bc85"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_statuses_user_created", "statuses", ["user_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_statuses_user_created", table_name="statuses")

