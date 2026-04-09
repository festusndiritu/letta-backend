"""section9 indexes part1

Revision ID: a1c0f4d8b901
Revises: 09cb3f303406
Create Date: 2026-04-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "a1c0f4d8b901"
down_revision: Union[str, None] = "09cb3f303406"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_messages_conv_created", "messages", ["conversation_id", "created_at"], unique=False)
    op.create_index("ix_receipts_user_read", "receipts", ["user_id", "read_at"], unique=False)
    op.create_index("ix_contacts_contact_id", "contacts", ["contact_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_contacts_contact_id", table_name="contacts")
    op.drop_index("ix_receipts_user_read", table_name="receipts")
    op.drop_index("ix_messages_conv_created", table_name="messages")

