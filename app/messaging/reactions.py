"""
Reactions.

One reaction per user per message (toggle behaviour — reacting with the
same emoji removes it, reacting with a different emoji replaces it).

WS events:
  reaction.add    → server → all conversation members
  reaction.remove → server → all conversation members
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.messaging.connection import manager
from app.messaging.service import _get_conversation_member_ids as get_conversation_member_ids
from app.models import Member, Message, Reaction, User

router = APIRouter()

# Allowed emojis — keep it focused, not a full picker
ALLOWED_EMOJIS = {"👍", "❤️", "😂", "😮", "😢", "🔥"}


class ReactIn(BaseModel):
    emoji: str


class ReactionOut(BaseModel):
    message_id: uuid.UUID
    user_id: uuid.UUID
    emoji: str


@router.post("/messages/{message_id}/react", response_model=ReactionOut | None)
async def react_to_message(
    message_id: uuid.UUID,
    body: ReactIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.emoji not in ALLOWED_EMOJIS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Emoji not allowed. Use one of: {', '.join(ALLOWED_EMOJIS)}",
        )

    # Load message and verify user is in the conversation
    result = await db.execute(select(Message).where(Message.id == message_id))
    message = result.scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")

    member_check = await db.execute(
        select(Member).where(
            Member.conversation_id == message.conversation_id,
            Member.user_id == current_user.id,
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this conversation.")

    # Load existing reaction
    result = await db.execute(
        select(Reaction).where(
            Reaction.message_id == message_id,
            Reaction.user_id == current_user.id,
        )
    )
    existing = result.scalar_one_or_none()

    member_ids = await get_conversation_member_ids(message.conversation_id, db)

    if existing:
        if existing.emoji == body.emoji:
            # Same emoji — remove (toggle off)
            await db.delete(existing)
            await db.flush()
            for mid in member_ids:
                await manager.send(mid, {
                    "type": "reaction.remove",
                    "payload": {
                        "message_id": str(message_id),
                        "user_id": str(current_user.id),
                    },
                })
            return None
        else:
            # Different emoji — replace
            existing.emoji = body.emoji
            await db.flush()
            reaction = existing
    else:
        reaction = Reaction(
            message_id=message_id,
            user_id=current_user.id,
            emoji=body.emoji,
        )
        db.add(reaction)
        await db.flush()

    for mid in member_ids:
        await manager.send(mid, {
            "type": "reaction.add",
            "payload": {
                "message_id": str(message_id),
                "user_id": str(current_user.id),
                "emoji": reaction.emoji,
            },
        })

    return ReactionOut(
        message_id=message_id,
        user_id=current_user.id,
        emoji=reaction.emoji,
    )