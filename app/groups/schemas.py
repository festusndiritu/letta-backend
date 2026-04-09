import uuid
from datetime import datetime

from pydantic import BaseModel

from app.messaging.schemas import MessageOut


class CreateDirectConversationIn(BaseModel):
    other_user_id: uuid.UUID


class CreateGroupConversationIn(BaseModel):
    name: str
    member_ids: list[uuid.UUID]  # does not need to include self — added automatically


class MemberOut(BaseModel):
    user_id: uuid.UUID
    display_name: str
    avatar_url: str | None
    role: str

    model_config = {"from_attributes": True}


class ConversationOut(BaseModel):
    id: uuid.UUID
    type: str
    name: str | None
    avatar_url: str | None
    created_at: datetime
    members: list[MemberOut]
    last_message: MessageOut | None = None
    unread_count: int = 0

    model_config = {"from_attributes": True}


class UpdateGroupIn(BaseModel):
    name: str | None = None
    avatar_url: str | None = None


class AddMembersIn(BaseModel):
    user_ids: list[uuid.UUID]


class RemoveMemberIn(BaseModel):
    user_id: uuid.UUID