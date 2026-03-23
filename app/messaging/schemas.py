import uuid
from datetime import datetime

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Inbound events (client → server)
# ---------------------------------------------------------------------------

class SendMessageEvent(BaseModel):
    conversation_id: uuid.UUID
    type: str  # text | image | video | audio | document
    content: str | None = None
    media_url: str | None = None
    media_mime: str | None = None
    reply_to_id: uuid.UUID | None = None


class AckEvent(BaseModel):
    message_id: uuid.UUID


class ReadEvent(BaseModel):
    message_id: uuid.UUID
    conversation_id: uuid.UUID


class TypingEvent(BaseModel):
    conversation_id: uuid.UUID


class InboundEvent(BaseModel):
    type: str  # message.send | message.ack | message.read | typing.start | typing.stop
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

    model_config = {"from_attributes": True}


class MessageHistoryParams(BaseModel):
    before_id: uuid.UUID | None = None  # cursor — load messages before this id
    limit: int = 30