import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


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
    call_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    conversation_id: uuid.UUID
    callee_id: uuid.UUID
    type: str
    sdp: dict

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, data):
        if not isinstance(data, dict):
            return data

        # Accept legacy/mobile keys used by earlier client builds.
        if "callee_id" not in data:
            data["callee_id"] = data.get("target_user_id") or data.get("targetUserId")
        if "type" not in data:
            data["type"] = data.get("call_type") or data.get("callType")
        if "sdp" not in data:
            data["sdp"] = data.get("offer")
        if "call_id" not in data:
            legacy_call_id = data.get("callId")
            if legacy_call_id is not None:
                data["call_id"] = legacy_call_id

        return data


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