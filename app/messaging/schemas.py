import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Inbound events (client → server)
# ---------------------------------------------------------------------------

class SendMessageEvent(BaseModel):
    conversation_id: uuid.UUID
    type: str  # text | image | video | audio | document | poll
    content: str | None = None
    media_url: str | None = None
    media_mime: str | None = None
    reply_to_id: uuid.UUID | None = None
    poll_data: str | None = None


class AckEvent(BaseModel):
    message_id: uuid.UUID


class ReadEvent(BaseModel):
    message_id: uuid.UUID
    conversation_id: uuid.UUID


class TypingEvent(BaseModel):
    conversation_id: uuid.UUID


class InboundEvent(BaseModel):
    type: str  # ping | message.* | typing.* | call.*
    payload: dict


# ---------------------------------------------------------------------------
# Outbound events (server → client)
# ---------------------------------------------------------------------------

class MessageOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    sender_id: uuid.UUID
    type: str
    content: str | None
    media_url: str | None
    media_mime: str | None
    reply_to_id: uuid.UUID | None
    created_at: datetime
    deleted_at: datetime | None = None
    reactions: dict[str, int] = Field(default_factory=dict)
    my_reaction: str | None = None
    poll_data: str | None = None

    model_config = {"from_attributes": True}


class CallOfferPayload(BaseModel):
    call_id: uuid.UUID
    conversation_id: uuid.UUID
    callee_id: uuid.UUID
    type: str
    sdp: dict


class CallAnswerPayload(BaseModel):
    call_id: uuid.UUID
    sdp: dict


class CallIcePayload(BaseModel):
    call_id: uuid.UUID
    target_user_id: uuid.UUID
    candidate: dict


class CallSimplePayload(BaseModel):
    call_id: uuid.UUID


class MessageHistoryParams(BaseModel):
    before_id: uuid.UUID | None = None  # cursor — load messages before this id
    limit: int = 30