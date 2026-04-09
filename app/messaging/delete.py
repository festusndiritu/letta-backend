import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.messaging.connection import manager
from app.messaging.service import _get_conversation_member_ids
from app.models import Message, User

router = APIRouter()


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message_for_everyone(
    message_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Message).where(Message.id == message_id))
    message = result.scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")

    if message.sender_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only sender can delete this message.")

    if datetime.now(UTC) - message.created_at > timedelta(minutes=60):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Delete window expired. Messages can only be deleted within 60 minutes.",
        )

    message.deleted_at = datetime.now(UTC)
    message.content = None
    message.media_url = None
    message.media_mime = None
    await db.flush()

    member_ids = await _get_conversation_member_ids(message.conversation_id, db)
    event = {
        "type": "message.deleted",
        "payload": {
            "message_id": str(message_id),
            "conversation_id": str(message.conversation_id),
        },
    }
    for member_id in member_ids:
        await manager.send(member_id, event)

