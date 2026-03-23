"""
Dashboard router.

Basic server stats — total users, active today, messages sent, storage.
Protected by a static admin token set in the environment so it's never
accidentally exposed. Not a full admin panel — just enough to know the
server is healthy and growing.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.messaging.connection import manager
from app.models import Message, User

router = APIRouter()

api_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


async def require_admin(key: str | None = Security(api_key_header)):
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard not configured. Set ADMIN_API_KEY in environment.",
        )
    if key != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin key.",
        )


class DashboardOut(BaseModel):
    total_users: int
    active_today: int
    total_messages: int
    messages_today: int
    online_now: int


@router.get("/dashboard", response_model=DashboardOut, dependencies=[Depends(require_admin)])
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Total users
    result = await db.execute(select(func.count()).select_from(User))
    total_users = result.scalar_one()

    # Active today (last_seen >= start of today)
    result = await db.execute(
        select(func.count()).select_from(User).where(User.last_seen >= today_start)
    )
    active_today = result.scalar_one()

    # Total messages
    result = await db.execute(select(func.count()).select_from(Message))
    total_messages = result.scalar_one()

    # Messages today
    result = await db.execute(
        select(func.count()).select_from(Message).where(Message.created_at >= today_start)
    )
    messages_today = result.scalar_one()

    # Currently online (connected WebSockets)
    online_now = len(manager.online_user_ids())

    return DashboardOut(
        total_users=total_users,
        active_today=active_today,
        total_messages=total_messages,
        messages_today=messages_today,
        online_now=online_now,
    )