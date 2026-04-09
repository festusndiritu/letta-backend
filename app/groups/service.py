"""
Conversations service.

Handles both direct (1-on-1) and group conversations.
Direct conversations are deduplicated — creating one between A and B
when one already exists returns the existing conversation.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.encryption import decrypt_maybe
from app.messaging.schemas import MessageOut
from app.models import Conversation, Member, User
from app.models import Message, Receipt


def _serialize_last_message(last_message: Message | None) -> MessageOut | None:
    if not last_message:
        return None

    return MessageOut(
        id=last_message.id,
        conversation_id=last_message.conversation_id,
        sender_id=last_message.sender_id,
        type=last_message.type,
        content=last_message.content,
        media_url=last_message.media_url,
        media_mime=last_message.media_mime,
        reply_to_id=last_message.reply_to_id,
        created_at=last_message.created_at,
        deleted_at=last_message.deleted_at,
        poll_data=last_message.poll_data,
        reactions={},
        my_reaction=None,
    )


async def _load_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession,
) -> Conversation | None:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(
            selectinload(Conversation.members).selectinload(Member.user)
        )
    )
    return result.scalar_one_or_none()


async def get_or_create_direct(
    user_a: User,
    user_b_id: uuid.UUID,
    db: AsyncSession,
) -> tuple[Conversation, bool]:
    """
    Get existing direct conversation between two users, or create one.
    Returns (conversation, created).
    """
    # Find conversations where both users are members
    # Subquery: conversation_ids that user_a is in
    a_convs = select(Member.conversation_id).where(Member.user_id == user_a.id)
    # Subquery: conversation_ids that user_b is in
    b_convs = select(Member.conversation_id).where(Member.user_id == user_b_id)

    result = await db.execute(
        select(Conversation).where(
            and_(
                Conversation.type == "direct",
                Conversation.id.in_(a_convs),
                Conversation.id.in_(b_convs),
            )
        ).options(
            selectinload(Conversation.members).selectinload(Member.user)
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing, False

    # Create new direct conversation
    conversation = Conversation(type="direct", created_by=user_a.id)
    db.add(conversation)
    await db.flush()

    for uid in [user_a.id, user_b_id]:
        db.add(Member(conversation_id=conversation.id, user_id=uid, role="member"))

    await db.flush()

    conv = await _load_conversation(conversation.id, db)
    return conv, True


async def create_group(
    creator: User,
    name: str,
    member_ids: list[uuid.UUID],
    db: AsyncSession,
) -> Conversation:
    conversation = Conversation(
        type="group",
        name=name,
        created_by=creator.id,
    )
    db.add(conversation)
    await db.flush()

    # Creator is admin, everyone else is member
    all_ids = list(dict.fromkeys([creator.id] + member_ids))  # deduplicate, creator first
    for uid in all_ids:
        role = "admin" if uid == creator.id else "member"
        db.add(Member(conversation_id=conversation.id, user_id=uid, role=role))

    await db.flush()
    return await _load_conversation(conversation.id, db)


async def get_user_conversations(
    user: User,
    db: AsyncSession,
) -> list[tuple[Conversation, dict]]:
    result = await db.execute(
        select(Conversation)
        .join(Member, Member.conversation_id == Conversation.id)
        .where(Member.user_id == user.id)
        .options(
            selectinload(Conversation.members).selectinload(Member.user)
        )
        .order_by(Conversation.id)  # stable order; Android sorts by last message
    )
    conversations = list(result.scalars().unique().all())
    with_meta: list[tuple[Conversation, dict]] = []
    for conv in conversations:
        with_meta.append((conv, await get_conversation_meta(conv.id, user.id, db)))
    return with_meta


async def get_conversation_meta(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> dict:
    last_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    last_message = last_result.scalar_one_or_none()
    if last_message and last_message.content:
        last_message.content = decrypt_maybe(last_message.content)

    last_read_result = await db.execute(
        select(func.max(Receipt.read_at))
        .join(Message, Message.id == Receipt.message_id)
        .where(
            Receipt.user_id == user_id,
            Message.conversation_id == conversation_id,
        )
    )
    last_read_at = last_read_result.scalar_one_or_none()
    if last_read_at is None:
        last_read_at = datetime(1970, 1, 1, tzinfo=UTC)

    unread_result = await db.execute(
        select(func.count(Message.id)).where(
            Message.conversation_id == conversation_id,
            Message.sender_id != user_id,
            Message.created_at > last_read_at,
        )
    )
    unread_count = unread_result.scalar_one() or 0

    return {
        "last_message": _serialize_last_message(last_message),
        "unread_count": unread_count,
    }


async def get_conversation_for_user(
    conversation_id: uuid.UUID,
    user: User,
    db: AsyncSession,
) -> Conversation | None:
    result = await db.execute(
        select(Member).where(
            Member.conversation_id == conversation_id,
            Member.user_id == user.id,
        )
    )
    if not result.scalar_one_or_none():
        return None
    return await _load_conversation(conversation_id, db)


async def update_group(
    conversation_id: uuid.UUID,
    requester: User,
    name: str | None,
    avatar_url: str | None,
    db: AsyncSession,
) -> Conversation:
    result = await db.execute(
        select(Member).where(
            Member.conversation_id == conversation_id,
            Member.user_id == requester.id,
            Member.role == "admin",
        )
    )
    if not result.scalar_one_or_none():
        raise PermissionError("Only admins can update group info.")

    conv = await _load_conversation(conversation_id, db)
    if not conv or conv.type != "group":
        raise ValueError("Group not found.")

    if name is not None:
        conv.name = name
    if avatar_url is not None:
        conv.avatar_url = avatar_url

    await db.flush()
    return await _load_conversation(conversation_id, db)


async def add_members(
    conversation_id: uuid.UUID,
    requester: User,
    user_ids: list[uuid.UUID],
    db: AsyncSession,
) -> Conversation:
    result = await db.execute(
        select(Member).where(
            Member.conversation_id == conversation_id,
            Member.user_id == requester.id,
            Member.role == "admin",
        )
    )
    if not result.scalar_one_or_none():
        raise PermissionError("Only admins can add members.")

    for uid in user_ids:
        existing = await db.execute(
            select(Member).where(
                Member.conversation_id == conversation_id,
                Member.user_id == uid,
            )
        )
        if not existing.scalar_one_or_none():
            db.add(Member(conversation_id=conversation_id, user_id=uid, role="member"))

    await db.flush()
    return await _load_conversation(conversation_id, db)


async def remove_member(
    conversation_id: uuid.UUID,
    requester: User,
    target_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    # Admins can remove anyone; members can only remove themselves (leave)
    result = await db.execute(
        select(Member).where(
            Member.conversation_id == conversation_id,
            Member.user_id == requester.id,
        )
    )
    requester_member = result.scalar_one_or_none()
    if not requester_member:
        raise PermissionError("Not a member of this conversation.")

    if requester.id != target_id and requester_member.role != "admin":
        raise PermissionError("Only admins can remove other members.")

    result = await db.execute(
        select(Member).where(
            Member.conversation_id == conversation_id,
            Member.user_id == target_id,
        )
    )
    target_member = result.scalar_one_or_none()
    if target_member:
        await db.delete(target_member)
        await db.flush()