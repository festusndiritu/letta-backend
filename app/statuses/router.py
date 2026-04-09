import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.encryption import decrypt_maybe, encrypt_maybe
from app.database import get_db
from app.messaging.connection import manager
from app.models import Contact, Status, StatusView, User

router = APIRouter()


class StatusCreateIn(BaseModel):
    type: str
    content: str | None = None
    media_url: str | None = None
    bg_color: str | None = None


class StatusOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    display_name: str
    avatar_url: str | None
    type: str
    content: str | None
    media_url: str | None
    bg_color: str | None
    created_at: datetime
    expires_at: datetime
    viewed: bool
    view_count: int | None = None


class UserStatusGroup(BaseModel):
    user_id: uuid.UUID
    display_name: str
    avatar_url: str | None
    statuses: list[StatusOut]
    all_viewed: bool


@router.post("/statuses", response_model=StatusOut, status_code=status.HTTP_201_CREATED)
async def create_status(
    body: StatusCreateIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.type not in {"text", "image", "video"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="type must be text, image, or video")
    if body.type == "text" and not (body.content or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="content is required for text status")
    if body.type in {"image", "video"} and not body.media_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="media_url is required for media statuses")

    now = datetime.now(UTC)
    status_row = Status(
        user_id=current_user.id,
        type=body.type,
        content=encrypt_maybe(body.content) if body.type == "text" else None,
        media_url=body.media_url,
        bg_color=body.bg_color,
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )
    db.add(status_row)
    await db.flush()

    watchers_result = await db.execute(select(Contact.owner_id).where(Contact.contact_id == current_user.id))
    event = {"type": "status.new", "payload": {"user_id": str(current_user.id)}}
    for watcher_id in watchers_result.scalars().all():
        await manager.send(watcher_id, event)

    return StatusOut(
        id=status_row.id,
        user_id=current_user.id,
        display_name=current_user.display_name,
        avatar_url=current_user.avatar_url,
        type=status_row.type,
        content=body.content,
        media_url=status_row.media_url,
        bg_color=status_row.bg_color,
        created_at=status_row.created_at,
        expires_at=status_row.expires_at,
        viewed=True,
        view_count=0,
    )


@router.get("/statuses/feed", response_model=list[UserStatusGroup])
async def statuses_feed(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(UTC)
    result = await db.execute(
        select(Status, User, StatusView.viewer_id)
        .join(Contact, Contact.contact_id == Status.user_id)
        .join(User, User.id == Status.user_id)
        .outerjoin(
            StatusView,
            (StatusView.status_id == Status.id) & (StatusView.viewer_id == current_user.id),
        )
        .where(
            Contact.owner_id == current_user.id,
            Status.expires_at > now,
        )
        .order_by(Status.created_at.desc())
    )

    grouped: dict[uuid.UUID, dict] = defaultdict(lambda: {"statuses": [], "display_name": "", "avatar_url": None})
    for status_row, owner, viewed_by_me in result.all():
        content = decrypt_maybe(status_row.content) if status_row.content else None
        status_out = StatusOut(
            id=status_row.id,
            user_id=owner.id,
            display_name=owner.display_name,
            avatar_url=owner.avatar_url,
            type=status_row.type,
            content=content,
            media_url=status_row.media_url,
            bg_color=status_row.bg_color,
            created_at=status_row.created_at,
            expires_at=status_row.expires_at,
            viewed=viewed_by_me is not None,
            view_count=None,
        )
        grouped[owner.id]["display_name"] = owner.display_name
        grouped[owner.id]["avatar_url"] = owner.avatar_url
        grouped[owner.id]["statuses"].append(status_out)

    output: list[UserStatusGroup] = []
    for user_id, payload in grouped.items():
        statuses = payload["statuses"]
        output.append(
            UserStatusGroup(
                user_id=user_id,
                display_name=payload["display_name"],
                avatar_url=payload["avatar_url"],
                statuses=statuses,
                all_viewed=bool(statuses) and all(item.viewed for item in statuses),
            )
        )
    return output


@router.get("/statuses/mine", response_model=list[StatusOut])
async def my_statuses(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(UTC)
    result = await db.execute(
        select(Status, func.count(StatusView.viewer_id))
        .outerjoin(StatusView, StatusView.status_id == Status.id)
        .where(
            Status.user_id == current_user.id,
            Status.expires_at > now,
        )
        .group_by(Status.id)
        .order_by(Status.created_at.desc())
    )

    rows = []
    for status_row, view_count in result.all():
        rows.append(
            StatusOut(
                id=status_row.id,
                user_id=current_user.id,
                display_name=current_user.display_name,
                avatar_url=current_user.avatar_url,
                type=status_row.type,
                content=decrypt_maybe(status_row.content) if status_row.content else None,
                media_url=status_row.media_url,
                bg_color=status_row.bg_color,
                created_at=status_row.created_at,
                expires_at=status_row.expires_at,
                viewed=True,
                view_count=int(view_count or 0),
            )
        )
    return rows


@router.post("/statuses/{status_id}/view", status_code=status.HTTP_204_NO_CONTENT)
async def mark_status_viewed(
    status_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Status).where(Status.id == status_id))
    status_row = result.scalar_one_or_none()
    if not status_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Status not found.")

    existing = await db.execute(
        select(StatusView).where(
            StatusView.status_id == status_id,
            StatusView.viewer_id == current_user.id,
        )
    )
    row = existing.scalar_one_or_none()
    if row:
        row.viewed_at = datetime.now(UTC)
    else:
        db.add(StatusView(status_id=status_id, viewer_id=current_user.id, viewed_at=datetime.now(UTC)))
    await db.flush()


@router.delete("/statuses/{status_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_status(
    status_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Status).where(
            Status.id == status_id,
            Status.user_id == current_user.id,
        )
    )
    status_row = result.scalar_one_or_none()
    if not status_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Status not found.")

    await db.delete(status_row)

