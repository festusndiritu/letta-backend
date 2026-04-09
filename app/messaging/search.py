"""
Search and discovery endpoints.

GET /conversations/{id}/messages/search?q= — full-text search in a conversation
GET /users/search?q=                        — find users by display name
GET /users/{id}                             — public profile
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.encryption import decrypt_maybe
from app.database import get_db
from app.messaging.schemas import MessageOut
from app.models import Member, Message, User

router = APIRouter()


class PublicUserOut(BaseModel):
    id: uuid.UUID
    display_name: str
    bio: str | None
    avatar_url: str | None

    model_config = {"from_attributes": True}


@router.get("/conversations/{conversation_id}/messages/search", response_model=list[MessageOut])
async def search_messages(
    conversation_id: uuid.UUID,
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(30, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify membership
    result = await db.execute(
        select(Member).where(
            Member.conversation_id == conversation_id,
            Member.user_id == current_user.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member.")

    # Note: searching encrypted content requires decrypting in Python.
    # For now we do a DB ILIKE on content — works because content is encrypted
    # per-message with a different nonce so ILIKE won't match ciphertext.
    # Real solution: maintain a separate plaintext FTS index, or decrypt + filter in Python.
    # We fetch recent messages and filter in Python — fine for V1 scale.
    result = await db.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.type == "text",
        )
        .order_by(Message.created_at.desc())
        .limit(500)  # search within last 500 messages
    )
    messages = result.scalars().all()

    matched = []
    q_lower = q.lower()
    for msg in messages:
        if msg.deleted_at:
            msg.content = None
            msg.media_url = None
            matched.append(msg)
            if len(matched) >= limit:
                break
            continue

        plaintext = decrypt_maybe(msg.content) or ""
        if q_lower in plaintext.lower():
            # Return with decrypted content for the response
            msg.content = plaintext
            matched.append(msg)
        if len(matched) >= limit:
            break

    return matched


@router.get("/users/search", response_model=list[PublicUserOut])
async def search_users(
    q: str = Query(..., min_length=1, max_length=50),
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search users by display name. Useful for finding people not in contacts."""
    result = await db.execute(
        select(User)
        .where(
            User.display_name.ilike(f"%{q}%"),
            User.id != current_user.id,
        )
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/users/{user_id}", response_model=PublicUserOut)
async def get_user_profile(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return user