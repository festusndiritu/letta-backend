"""
Session management.

GET  /sessions        — list all active sessions for current user
DELETE /sessions/{id} — revoke a specific session (closes WS if active)
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.messaging.connection import manager
from app.models import Session, User

router = APIRouter()


class SessionOut(BaseModel):
    id: uuid.UUID
    device_name: str | None
    created_at: datetime
    last_active_at: datetime
    is_current: bool  # True if this session has an active WS connection


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Session)
        .where(Session.user_id == current_user.id)
        .order_by(Session.last_active_at.desc())
    )
    sessions = result.scalars().all()
    active_ids = {s.session_id for s in manager.get_sessions(current_user.id)}

    return [
        SessionOut(
            id=s.id,
            device_name=s.device_name,
            created_at=s.created_at,
            last_active_at=s.last_active_at,
            is_current=s.id in active_ids,
        )
        for s in sessions
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Session).where(
            Session.id == session_id,
            Session.user_id == current_user.id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")

    # Close WS if active
    await manager.disconnect_session(current_user.id, session_id)

    # Remove from DB
    await db.delete(session)