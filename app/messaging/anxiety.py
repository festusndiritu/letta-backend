"""
Anxiety controls.

Focus profiles (per user, global):
  normal  — everything works as configured
  quiet   — presence not broadcast, FCM low priority
  off     — WS connection closed, FCM silent

Mute (per conversation):
  muted_until = NULL      → not muted
  muted_until = timestamp → muted until that time
  muted_until = far future (year 9999) → muted forever
"""

from datetime import UTC, datetime, timedelta
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.messaging.connection import manager
from app.models import Conversation, Member, User
import uuid

router = APIRouter()


class FocusProfile(str, Enum):
    normal = "normal"
    quiet = "quiet"
    off = "off"


class MuteDuration(str, Enum):
    one_hour = "1h"
    eight_hours = "8h"
    one_week = "1w"
    always = "always"


class SetFocusIn(BaseModel):
    profile: FocusProfile


class MuteIn(BaseModel):
    duration: MuteDuration


class DisappearIn(BaseModel):
    seconds: int | None = None


_MUTE_DELTAS = {
    MuteDuration.one_hour: timedelta(hours=1),
    MuteDuration.eight_hours: timedelta(hours=8),
    MuteDuration.one_week: timedelta(weeks=1),
}

# "Always" muted — far future date
_FOREVER = datetime(9999, 12, 31, tzinfo=UTC)


@router.patch("/users/me/focus", response_model=dict)
async def set_focus_profile(
    body: SetFocusIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Update all member rows for this user to the new notification profile
    result = await db.execute(
        select(Member).where(Member.user_id == current_user.id)
    )
    members = result.scalars().all()
    for member in members:
        member.notification_profile = body.profile.value

    await db.flush()

    # If switching to "off", close the WebSocket connection
    # The Android app sees the close and knows to stop trying until user switches back
    if body.profile == FocusProfile.off:
        for entry in manager.get_sessions(current_user.id):
            try:
                await entry.websocket.close(code=4002, reason="Focus mode: off")
            except Exception:
                pass
            manager.disconnect(current_user.id, entry.session_id)

    return {"profile": body.profile.value}


@router.post("/conversations/{conversation_id}/mute", status_code=status.HTTP_204_NO_CONTENT)
async def mute_conversation(
    conversation_id: uuid.UUID,
    body: MuteIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Member).where(
            Member.conversation_id == conversation_id,
            Member.user_id == current_user.id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    if body.duration == MuteDuration.always:
        member.muted_until = _FOREVER
    else:
        member.muted_until = datetime.now(UTC) + _MUTE_DELTAS[body.duration]

    await db.flush()


@router.delete("/conversations/{conversation_id}/mute", status_code=status.HTTP_204_NO_CONTENT)
async def unmute_conversation(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Member).where(
            Member.conversation_id == conversation_id,
            Member.user_id == current_user.id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    member.muted_until = None
    await db.flush()


@router.patch("/conversations/{conversation_id}/disappear", response_model=dict)
async def set_disappearing_timer(
    conversation_id: uuid.UUID,
    body: DisappearIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    allowed = {None, 3600, 86400, 604800}
    if body.seconds not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="seconds must be one of: null, 3600, 86400, 604800",
        )

    member_result = await db.execute(
        select(Member).where(
            Member.conversation_id == conversation_id,
            Member.user_id == current_user.id,
        )
    )
    member = member_result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member.")

    conv_result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conversation = conv_result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    if conversation.type == "group" and member.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admins can set disappear timer.")

    if conversation.type == "direct":
        all_members = await db.execute(select(Member).where(Member.conversation_id == conversation_id))
        for row in all_members.scalars().all():
            row.disappear_after_seconds = body.seconds
    else:
        member.disappear_after_seconds = body.seconds

    await db.flush()
    return {"conversation_id": str(conversation_id), "seconds": body.seconds}
