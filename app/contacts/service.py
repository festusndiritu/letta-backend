"""
Contacts service.

Contact sync privacy model:
  - Raw phone numbers never leave the device
  - Client hashes all numbers with SHA-256 before sending
  - Server matches hashes against phone_hash column
  - Only matched (registered) users are returned
  - No raw numbers stored or logged server-side
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, User


async def sync_contacts(
    owner: User,
    phone_hashes: list[str],
    db: AsyncSession,
) -> list[dict]:
    """
    Match phone_hashes against registered users.
    Upserts a Contact row for each match.
    Returns list of matched user info.
    """
    if not phone_hashes:
        return []

    # Find registered users matching any of the hashes
    result = await db.execute(
        select(User).where(
            User.phone_hash.in_(phone_hashes),
            User.id != owner.id,  # exclude self
        )
    )
    matched_users = result.scalars().all()

    # Upsert contact rows for each match
    for matched_user in matched_users:
        existing = await db.execute(
            select(Contact).where(
                Contact.owner_id == owner.id,
                Contact.contact_id == matched_user.id,
            )
        )
        if not existing.scalar_one_or_none():
            contact = Contact(
                owner_id=owner.id,
                contact_id=matched_user.id,
            )
            db.add(contact)

    await db.flush()

    return [
        {
            "user_id": u.id,
            "display_name": u.display_name,
            "avatar_url": u.avatar_url,
            "phone_hash": u.phone_hash,
        }
        for u in matched_users
    ]


async def block_user(
    owner: User,
    target_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    """
    Block a user. Creates a contact row if one doesn't exist,
    then sets blocked_at. Server silently drops messages from
    blocked users at delivery time.
    """
    result = await db.execute(
        select(Contact).where(
            Contact.owner_id == owner.id,
            Contact.contact_id == target_id,
        )
    )
    contact = result.scalar_one_or_none()

    if not contact:
        contact = Contact(owner_id=owner.id, contact_id=target_id)
        db.add(contact)

    contact.blocked_at = datetime.now(UTC)
    await db.flush()


async def unblock_user(
    owner: User,
    target_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    result = await db.execute(
        select(Contact).where(
            Contact.owner_id == owner.id,
            Contact.contact_id == target_id,
        )
    )
    contact = result.scalar_one_or_none()
    if contact:
        contact.blocked_at = None
        await db.flush()


async def is_blocked(
    sender_id: uuid.UUID,
    recipient_id: uuid.UUID,
    db: AsyncSession,
) -> bool:
    """Check if recipient has blocked sender."""
    result = await db.execute(
        select(Contact).where(
            Contact.owner_id == recipient_id,
            Contact.contact_id == sender_id,
            Contact.blocked_at.isnot(None),
        )
    )
    return result.scalar_one_or_none() is not None