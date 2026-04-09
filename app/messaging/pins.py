import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.messaging.connection import manager
from app.messaging.schemas import MessageOut
from app.messaging.service import _get_conversation_member_ids, build_message_out_batch
from app.models import Member, Message, PinnedMessage, User

router = APIRouter()


class PinIn(BaseModel):
    message_id: uuid.UUID


async def _assert_can_pin(conversation_id: uuid.UUID, user: User, db: AsyncSession) -> None:
    member_result = await db.execute(
        select(Member).where(
            Member.conversation_id == conversation_id,
            Member.user_id == user.id,
        )
    )
    member = member_result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member.")

    # Direct conversations: both parties can pin. Groups: admins only.
    conv_member_count = await db.execute(
        select(Member.user_id).where(Member.conversation_id == conversation_id)
    )
    if len(conv_member_count.scalars().all()) > 2 and member.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admins can pin in groups.")


@router.post("/conversations/{conversation_id}/pins", status_code=status.HTTP_204_NO_CONTENT)
async def pin_message(
    conversation_id: uuid.UUID,
    body: PinIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_can_pin(conversation_id, current_user, db)

    msg_result = await db.execute(select(Message).where(Message.id == body.message_id))
    message = msg_result.scalar_one_or_none()
    if not message or message.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found in conversation.")

    existing = await db.execute(
        select(PinnedMessage).where(
            PinnedMessage.conversation_id == conversation_id,
            PinnedMessage.message_id == body.message_id,
        )
    )
    if existing.scalar_one_or_none():
        return

    db.add(
        PinnedMessage(
            conversation_id=conversation_id,
            message_id=body.message_id,
            pinned_by=current_user.id,
        )
    )
    await db.flush()

    member_ids = await _get_conversation_member_ids(conversation_id, db)
    event = {
        "type": "message.pinned",
        "payload": {
            "conversation_id": str(conversation_id),
            "message_id": str(body.message_id),
            "pinned_by": str(current_user.id),
        },
    }
    for member_id in member_ids:
        await manager.send(member_id, event)


@router.delete("/conversations/{conversation_id}/pins/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unpin_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_can_pin(conversation_id, current_user, db)

    result = await db.execute(
        select(PinnedMessage).where(
            PinnedMessage.conversation_id == conversation_id,
            PinnedMessage.message_id == message_id,
        )
    )
    pinned = result.scalar_one_or_none()
    if not pinned:
        return

    await db.delete(pinned)
    await db.flush()

    member_ids = await _get_conversation_member_ids(conversation_id, db)
    event = {
        "type": "message.unpinned",
        "payload": {
            "conversation_id": str(conversation_id),
            "message_id": str(message_id),
        },
    }
    for member_id in member_ids:
        await manager.send(member_id, event)


@router.get("/conversations/{conversation_id}/pins", response_model=list[MessageOut])
async def list_pins(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    member_result = await db.execute(
        select(Member).where(
            Member.conversation_id == conversation_id,
            Member.user_id == current_user.id,
        )
    )
    if not member_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member.")

    result = await db.execute(
        select(Message)
        .join(PinnedMessage, PinnedMessage.message_id == Message.id)
        .where(PinnedMessage.conversation_id == conversation_id)
        .order_by(PinnedMessage.pinned_at.desc())
    )
    messages = result.scalars().all()
    return await build_message_out_batch(messages, current_user.id, db)

