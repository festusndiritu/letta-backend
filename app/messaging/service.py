"""
Messaging service.

Handles all business logic for the realtime layer:
- Saving messages to the DB
- Routing to online recipients via the connection manager
- Sending FCM knocks to offline recipients
- Writing delivery/read receipts (only if both parties have receipts on)
- Presence broadcasting (only if user has presence_visible on)
- Typing indicator fan-out (ephemeral, never persisted)
"""

import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.connection import manager
from app.messaging.schemas import MessageOut, SendMessageEvent
from app.contacts.service import is_blocked
from app.core.encryption import decrypt_maybe, encrypt_maybe
from app.models import Contact, Member, Message, PushToken, Reaction, Receipt, User
from app.notifications.fcm import send_knock

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _message_to_out(
    message: Message,
    *,
    reactions: dict[str, int] | None = None,
    my_reaction: str | None = None,
) -> MessageOut:
    return MessageOut(
        id=message.id,
        conversation_id=message.conversation_id,
        sender_id=message.sender_id,
        type=message.type,
        content=message.content,
        media_url=message.media_url,
        media_mime=message.media_mime,
        reply_to_id=message.reply_to_id,
        created_at=message.created_at,
        deleted_at=message.deleted_at,
        poll_data=message.poll_data,
        reactions=reactions or {},
        my_reaction=my_reaction,
    )

async def _get_conversation_member_ids(
    conversation_id: uuid.UUID,
    db: AsyncSession,
) -> list[uuid.UUID]:
    result = await db.execute(
        select(Member.user_id).where(Member.conversation_id == conversation_id)
    )
    return list(result.scalars().all())


async def _get_user(user_id: uuid.UUID, db: AsyncSession) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def _get_fcm_token(user_id: uuid.UUID, db: AsyncSession) -> str | None:
    result = await db.execute(
        select(PushToken.fcm_token).where(PushToken.user_id == user_id)
    )
    return result.scalar_one_or_none()


def _build_knock_copy(sender: User, event: SendMessageEvent) -> tuple[str, str]:
    title = sender.display_name or "New message"

    if event.type == "text":
        body = (event.content or "New message").strip() or "New message"
    else:
        body_by_type = {
            "image": "Sent an image",
            "video": "Sent a video",
            "audio": "Sent an audio message",
            "document": "Sent a document",
        }
        body = body_by_type.get(event.type, "Sent a message")

    return title, body[:120]


async def build_message_out_batch(
    messages: list[Message],
    user_id: uuid.UUID,
    db: AsyncSession,
) -> list[MessageOut]:
    if not messages:
        return []

    for msg in messages:
        if msg.deleted_at:
            msg.content = None
            msg.media_url = None
        else:
            msg.content = decrypt_maybe(msg.content)

    await _attach_reactions_for_messages(messages, user_id, db)

    out: list[MessageOut] = []
    for msg in messages:
        reactions = getattr(msg, "reactions", {})
        if not isinstance(reactions, dict):
            reactions = {}
        out.append(
            _message_to_out(
                msg,
                reactions=reactions,
                my_reaction=getattr(msg, "my_reaction", None),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Message send
# ---------------------------------------------------------------------------

async def handle_send_message(
    sender: User,
    event: SendMessageEvent,
    db: AsyncSession,
) -> Message:
    """
    Save message -> fan-out to all conversation members -> FCM for offline users.
    Returns the saved Message ORM object.
    """
    # Verify sender is a member of the conversation
    result = await db.execute(
        select(Member).where(
            Member.conversation_id == event.conversation_id,
            Member.user_id == sender.id,
        )
    )
    if not result.scalar_one_or_none():
        raise PermissionError("You are not a member of this conversation.")

    # Save message
    message = Message(
        conversation_id=event.conversation_id,
        sender_id=sender.id,
        type=event.type,
        content=encrypt_maybe(event.content),
        media_url=event.media_url,
        media_mime=event.media_mime,
        reply_to_id=event.reply_to_id,
        poll_data=event.poll_data,
    )

    disappear_result = await db.execute(
        select(Member.disappear_after_seconds).where(
            Member.conversation_id == event.conversation_id,
            Member.user_id == sender.id,
        )
    )
    disappear_after = disappear_result.scalar_one_or_none()
    if disappear_after:
        message.expires_at = datetime.now(UTC) + timedelta(seconds=disappear_after)

    db.add(message)
    await db.flush()

    # Decrypt for outbound — TLS protects the wire, DB stores ciphertext
    message.content = decrypt_maybe(message.content)
    message_out = _message_to_out(message, reactions={}, my_reaction=None).model_dump(mode="json")
    outbound_event = {"type": "message.new", "payload": message_out}

    # Fan-out to all members
    member_ids = await _get_conversation_member_ids(event.conversation_id, db)

    for member_id in member_ids:
        if member_id == sender.id:
            continue

        # Silently drop if recipient has blocked sender
        if await is_blocked(sender.id, member_id, db):
            continue

        delivered = await manager.send(member_id, outbound_event)

        if delivered:
            recipient = await _get_user(member_id, db)
            if recipient and recipient.receipts_visible and sender.receipts_visible:
                receipt = Receipt(
                    message_id=message.id,
                    user_id=member_id,
                    delivered_at=datetime.now(UTC),
                )
                db.add(receipt)
                await manager.send(sender.id, {
                    "type": "message.delivered",
                    "payload": {
                        "message_id": str(message.id),
                        "user_id": str(member_id),
                        "delivered_at": datetime.now(UTC).isoformat(),
                    },
                })
        else:
            # Check focus profile and mute before sending FCM knock
            member_result = await db.execute(
                select(Member).where(
                    Member.conversation_id == event.conversation_id,
                    Member.user_id == member_id,
                )
            )
            member_row = member_result.scalar_one_or_none()
            if member_row:
                # Off mode — no knock at all
                if member_row.notification_profile == "off":
                    continue
                # Muted — no knock
                if member_row.muted_until and member_row.muted_until > datetime.now(UTC):
                    continue

            fcm_token = await _get_fcm_token(member_id, db)
            if fcm_token:
                title, body = _build_knock_copy(sender, event)
                await send_knock(
                    fcm_token=fcm_token,
                    conversation_id=str(event.conversation_id),
                    title=title,
                    body=body,
                )

    return message


# ---------------------------------------------------------------------------
# Delivery ACK
# ---------------------------------------------------------------------------

async def handle_ack(
    user: User,
    message_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    result = await db.execute(select(Message).where(Message.id == message_id))
    message = result.scalar_one_or_none()
    if not message:
        return

    sender = await _get_user(message.sender_id, db)
    if not sender:
        return

    if not (user.receipts_visible and sender.receipts_visible):
        return

    result = await db.execute(
        select(Receipt).where(
            Receipt.message_id == message_id,
            Receipt.user_id == user.id,
        )
    )
    receipt = result.scalar_one_or_none()
    if not receipt:
        receipt = Receipt(message_id=message_id, user_id=user.id)
        db.add(receipt)
    if not receipt.delivered_at:
        receipt.delivered_at = datetime.now(UTC)

    await db.flush()

    await manager.send(message.sender_id, {
        "type": "message.delivered",
        "payload": {
            "message_id": str(message_id),
            "user_id": str(user.id),
            "delivered_at": receipt.delivered_at.isoformat(),
        },
    })


# ---------------------------------------------------------------------------
# Read receipt
# ---------------------------------------------------------------------------

async def handle_read(
    user: User,
    message_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    result = await db.execute(select(Message).where(Message.id == message_id))
    message = result.scalar_one_or_none()
    if not message:
        return

    sender = await _get_user(message.sender_id, db)
    if not sender:
        return

    if not (user.receipts_visible and sender.receipts_visible):
        return

    result = await db.execute(
        select(Receipt).where(
            Receipt.message_id == message_id,
            Receipt.user_id == user.id,
        )
    )
    receipt = result.scalar_one_or_none()
    if not receipt:
        receipt = Receipt(message_id=message_id, user_id=user.id)
        db.add(receipt)

    now = datetime.now(UTC)
    if not receipt.delivered_at:
        receipt.delivered_at = now
    receipt.read_at = now

    await db.flush()

    await manager.send(message.sender_id, {
        "type": "message.read",
        "payload": {
            "message_id": str(message_id),
            "user_id": str(user.id),
            "read_at": now.isoformat(),
        },
    })


# ---------------------------------------------------------------------------
# Typing indicators
# ---------------------------------------------------------------------------

async def handle_typing(
    sender: User,
    conversation_id: uuid.UUID,
    is_typing: bool,
    db: AsyncSession,
) -> None:
    """Ephemeral — never persisted. Gated on sender's receipts_visible setting."""
    if not sender.receipts_visible:
        return

    member_ids = await _get_conversation_member_ids(conversation_id, db)
    event = {
        "type": "typing.start" if is_typing else "typing.stop",
        "payload": {
            "conversation_id": str(conversation_id),
            "user_id": str(sender.id),
        },
    }
    for member_id in member_ids:
        if member_id != sender.id:
            await manager.send(member_id, event)


# ---------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------

async def broadcast_presence(
    user: User,
    online: bool,
    db: AsyncSession,
) -> None:
    """Only fires if user has presence_visible = True."""
    if not user.presence_visible:
        return

    now = datetime.now(UTC)
    user.last_seen = now
    db.add(user)

    result = await db.execute(
        select(Contact.owner_id).where(Contact.contact_id == user.id)
    )
    watcher_ids = result.scalars().all()

    event = {
        "type": "presence.update",
        "payload": {
            "user_id": str(user.id),
            "online": online,
            "last_seen": now.isoformat(),
        },
    }
    for watcher_id in watcher_ids:
        await manager.send(watcher_id, event)


# ---------------------------------------------------------------------------
# Message history (REST)
# ---------------------------------------------------------------------------

async def get_message_history(
    conversation_id: uuid.UUID,
    user: User,
    db: AsyncSession,
    before_id: uuid.UUID | None = None,
    limit: int = 30,
) -> list[MessageOut]:
    result = await db.execute(
        select(Member).where(
            Member.conversation_id == conversation_id,
            Member.user_id == user.id,
        )
    )
    if not result.scalar_one_or_none():
        raise PermissionError("You are not a member of this conversation.")

    query = select(Message).where(Message.conversation_id == conversation_id)

    if before_id:
        result = await db.execute(
            select(Message.created_at).where(Message.id == before_id)
        )
        cursor_ts = result.scalar_one_or_none()
        if cursor_ts:
            query = query.where(Message.created_at < cursor_ts)

    query = query.order_by(Message.created_at.desc()).limit(limit)
    result = await db.execute(query)
    messages = result.scalars().all()
    return await build_message_out_batch(messages, user.id, db)


async def _attach_reactions_for_messages(
    messages: list[Message],
    user_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    if not messages:
        return

    message_ids = [m.id for m in messages]
    result = await db.execute(
        select(Reaction.message_id, Reaction.user_id, Reaction.emoji)
        .where(Reaction.message_id.in_(message_ids))
    )

    counts: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
    my_reactions: dict[uuid.UUID, str] = {}
    for message_id, reactor_id, emoji in result.all():
        counts[message_id][emoji] = counts[message_id].get(emoji, 0) + 1
        if reactor_id == user_id:
            my_reactions[message_id] = emoji

    for message in messages:
        message.reactions = counts.get(message.id, {})
        message.my_reaction = my_reactions.get(message.id)

