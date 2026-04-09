import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.messaging.connection import manager
from app.messaging.service import _get_conversation_member_ids
from app.models import Member, Message, PollVote, User

router = APIRouter()


class VoteIn(BaseModel):
    option_indices: list[int]


@router.post("/messages/{message_id}/vote", status_code=status.HTTP_204_NO_CONTENT)
async def vote_on_poll(
    message_id: uuid.UUID,
    body: VoteIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Message).where(Message.id == message_id))
    message = result.scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")
    if message.type != "poll":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message is not a poll.")

    member_check = await db.execute(
        select(Member).where(
            Member.conversation_id == message.conversation_id,
            Member.user_id == current_user.id,
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member.")

    if not message.poll_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Poll data missing.")

    try:
        poll_data = json.loads(message.poll_data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Poll payload invalid.")

    options = poll_data.get("options") or []
    multiple = bool(poll_data.get("multiple_choice", False))
    if not isinstance(options, list) or not options:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Poll options invalid.")

    if not body.option_indices:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one option is required.")
    if not multiple and len(body.option_indices) > 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This poll allows only one choice.")

    clean_indices = sorted(set(body.option_indices))
    if any(index < 0 or index >= len(options) for index in clean_indices):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Option index out of range.")

    existing = await db.execute(
        select(PollVote).where(
            PollVote.message_id == message_id,
            PollVote.user_id == current_user.id,
        )
    )
    vote = existing.scalar_one_or_none()
    encoded = json.dumps(clean_indices)
    if vote:
        vote.option_indices = encoded
    else:
        db.add(PollVote(message_id=message_id, user_id=current_user.id, option_indices=encoded))
    await db.flush()

    votes_result = await db.execute(select(PollVote.option_indices).where(PollVote.message_id == message_id))
    counts: dict[int, int] = {i: 0 for i in range(len(options))}
    for raw in votes_result.scalars().all():
        try:
            indices = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for idx in indices:
            if isinstance(idx, int) and idx in counts:
                counts[idx] += 1

    member_ids = await _get_conversation_member_ids(message.conversation_id, db)
    event = {
        "type": "poll.vote",
        "payload": {
            "message_id": str(message_id),
            "conversation_id": str(message.conversation_id),
            "counts": {str(k): v for k, v in counts.items()},
        },
    }
    for member_id in member_ids:
        await manager.send(member_id, event)

