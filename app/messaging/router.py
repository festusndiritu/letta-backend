import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.service import decode_token
from app.database import get_db, AsyncSessionLocal
from app.messaging import service
from app.messaging.connection import manager
from app.messaging.schemas import (
    AckEvent,
    InboundEvent,
    MessageOut,
    ReadEvent,
    SendMessageEvent,
    TypingEvent,
)
from app.models import User

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    """
    Single persistent WebSocket per session.
    Auth: JWT access token passed as query param ?token=...
    """
    try:
        user_id_str = decode_token(token, expected_kind="access")
    except ValueError:
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    user_id = uuid.UUID(user_id_str)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            await websocket.close(code=4001, reason="User not found")
            return

        await manager.connect(user_id, websocket)
        await service.broadcast_presence(user, online=True, db=db)
        await db.commit()

        try:
            while True:
                data = await websocket.receive_json()

                try:
                    event = InboundEvent(**data)
                except Exception:
                    await websocket.send_json({
                        "type": "error",
                        "payload": {"detail": "Malformed event."},
                    })
                    continue

                async with AsyncSessionLocal() as event_db:
                    result = await event_db.execute(select(User).where(User.id == user_id))
                    current_user = result.scalar_one_or_none()
                    if not current_user:
                        break

                    try:
                        await _dispatch(event, current_user, event_db)
                        await event_db.commit()
                    except PermissionError as e:
                        await websocket.send_json({
                            "type": "error",
                            "payload": {"detail": str(e)},
                        })
                    except Exception as e:
                        logger.exception("Error handling WS event %s: %s", event.type, e)
                        await websocket.send_json({
                            "type": "error",
                            "payload": {"detail": "Internal error."},
                        })

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.exception("WebSocket error for user %s: %s", user_id, e)
        finally:
            manager.disconnect(user_id)
            async with AsyncSessionLocal() as offline_db:
                result = await offline_db.execute(select(User).where(User.id == user_id))
                offline_user = result.scalar_one_or_none()
                if offline_user:
                    await service.broadcast_presence(offline_user, online=False, db=offline_db)
                    await offline_db.commit()


async def _dispatch(event: InboundEvent, user: User, db: AsyncSession) -> None:
    if event.type == "message.send":
        send_event = SendMessageEvent(**event.payload)
        message = await service.handle_send_message(user, send_event, db)
        await manager.send(user.id, {
            "type": "message.sent",
            "payload": MessageOut.model_validate(message).model_dump(mode="json"),
        })

    elif event.type == "message.ack":
        ack = AckEvent(**event.payload)
        await service.handle_ack(user, ack.message_id, db)

    elif event.type == "message.read":
        read = ReadEvent(**event.payload)
        await service.handle_read(user, read.message_id, db)

    elif event.type in ("typing.start", "typing.stop"):
        typing = TypingEvent(**event.payload)
        await service.handle_typing(
            user,
            typing.conversation_id,
            is_typing=(event.type == "typing.start"),
            db=db,
        )

    else:
        raise ValueError(f"Unknown event type: {event.type}")


# ---------------------------------------------------------------------------
# REST — message history
# ---------------------------------------------------------------------------

@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def get_messages(
    conversation_id: uuid.UUID,
    before_id: uuid.UUID | None = Query(None),
    limit: int = Query(30, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        messages = await service.get_message_history(
            conversation_id, current_user, db, before_id=before_id, limit=limit
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    return messages


# ---------------------------------------------------------------------------
# REST — missed messages (called on reconnect)
# ---------------------------------------------------------------------------

@router.get("/messages/missed", response_model=list[MessageOut])
async def get_missed_messages(
    since: str = Query(..., description="ISO 8601 timestamp — get all messages after this point"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Called by Android immediately after reconnecting.
    Returns all messages across all the user's conversations
    that arrived after `since` (the last time the device was online).

    Android stores the timestamp of the last received message locally
    and passes it here on reconnect. This closes the gap between
    going offline and reconnecting without needing a message queue.
    """
    from datetime import datetime
    from sqlalchemy import select
    from app.models import Member, Message

    try:
        since_dt = datetime.fromisoformat(since)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid timestamp format. Use ISO 8601 e.g. 2026-01-01T00:00:00Z",
        )

    # Get all conversation IDs this user is a member of
    result = await db.execute(
        select(Member.conversation_id).where(Member.user_id == current_user.id)
    )
    conversation_ids = result.scalars().all()

    if not conversation_ids:
        return []

    # Fetch all messages in those conversations after since_dt
    result = await db.execute(
        select(Message)
        .where(
            Message.conversation_id.in_(conversation_ids),
            Message.created_at > since_dt,
            Message.sender_id != current_user.id,  # don't return own messages
        )
        .order_by(Message.created_at.asc())
        .limit(500)  # safety cap — if gap is huge, Android paginates further
    )
    return result.scalars().all()