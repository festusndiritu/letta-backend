import uuid
import logging
import asyncio
from datetime import UTC, datetime

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
    CallAnswerPayload,
    CallIcePayload,
    CallOfferPayload,
    CallSimplePayload,
    ReadEvent,
    SendMessageEvent,
    TypingEvent,
)
from app.models import Call, Member, User
from app.models import PushToken
from app.notifications.fcm import send_data_push

router = APIRouter()
logger = logging.getLogger(__name__)


async def receive_with_timeout(websocket: WebSocket, timeout: int = 90) -> dict:
    try:
        return await asyncio.wait_for(websocket.receive_json(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise WebSocketDisconnect(code=1001, reason="Connection timed out") from exc


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
    session_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            await websocket.close(code=4001, reason="User not found")
            return

        await manager.connect(user_id, websocket, session_id)
        await service.broadcast_presence(user, online=True, db=db)
        await db.commit()

        try:
            while True:
                data = await receive_with_timeout(websocket)

                try:
                    event = InboundEvent(**data)
                except Exception:
                    await websocket.send_json({
                        "type": "error",
                        "payload": {"detail": "Malformed event."},
                    })
                    continue

                if event.type == "ping":
                    await websocket.send_json({"type": "pong", "payload": {}})
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
            manager.disconnect(user_id, session_id)
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

    elif event.type == "call.offer":
        payload = CallOfferPayload(**event.payload)
        await _handle_call_offer(payload, user, db)

    elif event.type == "call.answer":
        payload = CallAnswerPayload(**event.payload)
        await _handle_call_answer(payload, user, db)

    elif event.type in ("call.ice-candidate", "call.ice_candidate"):
        payload = CallIcePayload(**event.payload)
        await _handle_call_ice(payload, user, db)

    elif event.type == "call.reject":
        payload = CallSimplePayload(**event.payload)
        await _handle_call_reject(payload, user, db)

    elif event.type == "call.end":
        payload = CallSimplePayload(**event.payload)
        await _handle_call_end(payload, user, db)

    else:
        raise ValueError(f"Unknown event type: {event.type}")


async def _assert_member(conversation_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> None:
    result = await db.execute(
        select(Member).where(
            Member.conversation_id == conversation_id,
            Member.user_id == user_id,
        )
    )
    if not result.scalar_one_or_none():
        raise PermissionError("Not a member of this conversation.")


async def _handle_call_offer(payload: CallOfferPayload, user: User, db: AsyncSession) -> None:
    await _assert_member(payload.conversation_id, user.id, db)
    await _assert_member(payload.conversation_id, payload.callee_id, db)

    call = Call(
        id=payload.call_id,
        conversation_id=payload.conversation_id,
        caller_id=user.id,
        callee_id=payload.callee_id,
        type=payload.type,
        status="ringing",
    )
    db.add(call)
    await db.flush()

    delivered = await manager.send(payload.callee_id, {
        "type": "call.offer",
        "payload": {
            "call_id": str(payload.call_id),
            "conversation_id": str(payload.conversation_id),
            "caller_id": str(user.id),
            "type": payload.type,
            "sdp": payload.sdp,
        },
    })

    if not delivered:
        token_result = await db.execute(
            select(PushToken.fcm_token).where(PushToken.user_id == payload.callee_id)
        )
        fcm_token = token_result.scalar_one_or_none()
        if fcm_token:
            await send_data_push(
                fcm_token,
                {
                    "type": "incoming_call",
                    "call_id": str(payload.call_id),
                    "caller_id": str(user.id),
                    "caller_name": user.display_name,
                    "call_type": payload.type,
                    "conversation_id": str(payload.conversation_id),
                },
                high_priority=True,
            )


async def _handle_call_answer(payload: CallAnswerPayload, user: User, db: AsyncSession) -> None:
    result = await db.execute(select(Call).where(Call.id == payload.call_id))
    call = result.scalar_one_or_none()
    if not call or call.callee_id != user.id:
        raise PermissionError("Call not found or unauthorized.")

    call.status = "answered"
    call.answered_at = datetime.now(UTC)
    await db.flush()

    await manager.send(call.caller_id, {
        "type": "call.answer",
        "payload": {
            "call_id": str(payload.call_id),
            "callee_id": str(user.id),
            "sdp": payload.sdp,
        },
    })


async def _handle_call_ice(payload: CallIcePayload, user: User, db: AsyncSession) -> None:
    result = await db.execute(select(Call).where(Call.id == payload.call_id))
    call = result.scalar_one_or_none()
    if not call or user.id not in {call.caller_id, call.callee_id}:
        raise PermissionError("Call not found or unauthorized.")
    if payload.target_user_id not in {call.caller_id, call.callee_id}:
        raise PermissionError("Invalid call target.")

    await manager.send(payload.target_user_id, {
        "type": "call.ice-candidate",
        "payload": {
            "call_id": str(payload.call_id),
            "from_user_id": str(user.id),
            "candidate": payload.candidate,
        },
    })


async def _handle_call_reject(payload: CallSimplePayload, user: User, db: AsyncSession) -> None:
    result = await db.execute(select(Call).where(Call.id == payload.call_id))
    call = result.scalar_one_or_none()
    if not call or call.callee_id != user.id:
        raise PermissionError("Call not found or unauthorized.")

    call.status = "rejected"
    call.ended_at = datetime.now(UTC)
    await db.flush()

    await manager.send(call.caller_id, {
        "type": "call.rejected",
        "payload": {
            "call_id": str(payload.call_id),
            "by": str(user.id),
        },
    })


async def _handle_call_end(payload: CallSimplePayload, user: User, db: AsyncSession) -> None:
    result = await db.execute(select(Call).where(Call.id == payload.call_id))
    call = result.scalar_one_or_none()
    if not call or user.id not in {call.caller_id, call.callee_id}:
        raise PermissionError("Call not found or unauthorized.")

    now = datetime.now(UTC)
    call.status = "ended"
    call.ended_at = now
    if call.answered_at:
        call.duration_seconds = int((now - call.answered_at).total_seconds())
    await db.flush()

    other_user = call.callee_id if user.id == call.caller_id else call.caller_id
    await manager.send(other_user, {
        "type": "call.ended",
        "payload": {
            "call_id": str(payload.call_id),
            "by": str(user.id),
            "duration_seconds": call.duration_seconds,
        },
    })


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

    result = await db.execute(
        select(Member.conversation_id).where(Member.user_id == current_user.id)
    )
    conversation_ids = result.scalars().all()

    if not conversation_ids:
        return []

    result = await db.execute(
        select(Message)
        .where(
            Message.conversation_id.in_(conversation_ids),
            Message.created_at > since_dt,
            Message.sender_id != current_user.id,
        )
        .order_by(Message.created_at.asc())
        .limit(500)
    )
    return result.scalars().all()


@router.get("/calls", response_model=list[dict])
async def get_call_history(
    limit: int = Query(20, ge=1, le=100),
    before_id: uuid.UUID | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Call).where((Call.caller_id == current_user.id) | (Call.callee_id == current_user.id))

    if before_id:
        cursor_result = await db.execute(select(Call.started_at).where(Call.id == before_id))
        cursor_ts = cursor_result.scalar_one_or_none()
        if cursor_ts:
            query = query.where(Call.started_at < cursor_ts)

    result = await db.execute(
        query.order_by(Call.started_at.desc()).limit(limit)
    )

    calls = []
    for call in result.scalars().all():
        calls.append(
            {
                "id": str(call.id),
                "conversation_id": str(call.conversation_id),
                "caller_id": str(call.caller_id),
                "callee_id": str(call.callee_id),
                "type": call.type,
                "status": call.status,
                "started_at": call.started_at.isoformat(),
                "answered_at": call.answered_at.isoformat() if call.answered_at else None,
                "ended_at": call.ended_at.isoformat() if call.ended_at else None,
                "duration_seconds": call.duration_seconds,
            }
        )
    return calls
