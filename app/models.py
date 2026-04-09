import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


def gen_uuid() -> uuid.UUID:
    return uuid.uuid4()


def now_tz():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

def dt_tz_nullable():
    return mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    phone_number: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    phone_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)          # new
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = now_tz()
    last_seen: Mapped[datetime | None] = dt_tz_nullable()

    # Anxiety controls — all off by default
    presence_visible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    receipts_visible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    show_timestamps: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    otp_codes: Mapped[list["OtpCode"]] = relationship(back_populates="user_ref", foreign_keys="OtpCode.phone_number", primaryjoin="User.phone_number == OtpCode.phone_number", viewonly=True)
    push_token: Mapped["PushToken | None"] = relationship(back_populates="user", uselist=False)
    memberships: Mapped[list["Member"]] = relationship(back_populates="user")
    sent_messages: Mapped[list["Message"]] = relationship(back_populates="sender")
    contacts_owned: Mapped[list["Contact"]] = relationship(back_populates="owner", foreign_keys="Contact.owner_id")
    sessions: Mapped[list["Session"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    reactions: Mapped[list["Reaction"]] = relationship(back_populates="user")
    statuses: Mapped[list["Status"]] = relationship(back_populates="user", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# Sessions (multi-device support — max 5 per user)
# ---------------------------------------------------------------------------

class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    device_name: Mapped[str | None] = mapped_column(Text, nullable=True)   # e.g. "Pixel 8", "Chrome on Mac"
    created_at: Mapped[datetime] = now_tz()
    last_active_at: Mapped[datetime] = now_tz()

    user: Mapped["User"] = relationship(back_populates="sessions")


# ---------------------------------------------------------------------------
# OTP Codes
# ---------------------------------------------------------------------------

class OtpCode(Base):
    __tablename__ = "otp_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    phone_number: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user_ref: Mapped["User | None"] = relationship(
        back_populates="otp_codes",
        primaryjoin="OtpCode.phone_number == User.phone_number",
        foreign_keys=[phone_number],
        viewonly=True,
    )


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("owner_id", "contact_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_at: Mapped[datetime | None] = dt_tz_nullable()

    owner: Mapped["User"] = relationship(back_populates="contacts_owned", foreign_keys=[owner_id])
    contact: Mapped["User"] = relationship(foreign_keys=[contact_id])


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (CheckConstraint("type IN ('direct', 'group')", name="conversations_type_check"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = now_tz()
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    members: Mapped[list["Member"]] = relationship(back_populates="conversation")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

class Member(Base):
    __tablename__ = "members"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'member')", name="members_role_check"),
        CheckConstraint("notification_profile IN ('normal', 'quiet', 'off')", name="members_notif_check"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(Text, default="member", nullable=False)
    joined_at: Mapped[datetime] = now_tz()
    muted_until: Mapped[datetime | None] = dt_tz_nullable()
    notification_profile: Mapped[str] = mapped_column(Text, default="normal", nullable=False)
    disappear_after_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    conversation: Mapped["Conversation"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("type IN ('text', 'image', 'video', 'audio', 'document', 'poll')", name="messages_type_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False, index=True)
    sender_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)       # encrypted at rest
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_mime: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_to_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("messages.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    expires_at: Mapped[datetime | None] = dt_tz_nullable()                 # set on delivery, cleanup job deletes past this
    deleted_at: Mapped[datetime | None] = dt_tz_nullable()
    poll_data: Mapped[str | None] = mapped_column(Text, nullable=True)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    sender: Mapped["User"] = relationship(back_populates="sent_messages")
    reply_to: Mapped["Message | None"] = relationship(remote_side="Message.id")
    receipts: Mapped[list["Receipt"]] = relationship(back_populates="message", cascade="all, delete-orphan")
    reactions: Mapped[list["Reaction"]] = relationship(back_populates="message", cascade="all, delete-orphan")
    poll_votes: Mapped[list["PollVote"]] = relationship(back_populates="message", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------

class Receipt(Base):
    __tablename__ = "receipts"

    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    delivered_at: Mapped[datetime | None] = dt_tz_nullable()
    read_at: Mapped[datetime | None] = dt_tz_nullable()

    message: Mapped["Message"] = relationship(back_populates="receipts")
    user: Mapped["User"] = relationship()


# ---------------------------------------------------------------------------
# Reactions
# ---------------------------------------------------------------------------

class Reaction(Base):
    __tablename__ = "reactions"
    __table_args__ = (UniqueConstraint("message_id", "user_id"),)          # one reaction per user per message

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    emoji: Mapped[str] = mapped_column(Text, nullable=False)               # e.g. "👍" "❤️" "😂"
    created_at: Mapped[datetime] = now_tz()

    message: Mapped["Message"] = relationship(back_populates="reactions")
    user: Mapped["User"] = relationship(back_populates="reactions")


# ---------------------------------------------------------------------------
# Push Tokens
# ---------------------------------------------------------------------------

class PushToken(Base):
    __tablename__ = "push_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    fcm_token: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user: Mapped["User"] = relationship(back_populates="push_token")


# ---------------------------------------------------------------------------
# Statuses
# ---------------------------------------------------------------------------

class Status(Base):
    __tablename__ = "statuses"
    __table_args__ = (CheckConstraint("type IN ('text', 'image', 'video')", name="statuses_type_check"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    bg_color: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = now_tz()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped["User"] = relationship(back_populates="statuses")
    views: Mapped[list["StatusView"]] = relationship(back_populates="status", cascade="all, delete-orphan")


class StatusView(Base):
    __tablename__ = "status_views"

    status_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("statuses.id", ondelete="CASCADE"), primary_key=True)
    viewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    status: Mapped["Status"] = relationship(back_populates="views")


# ---------------------------------------------------------------------------
# Message pinning
# ---------------------------------------------------------------------------

class PinnedMessage(Base):
    __tablename__ = "pinned_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "message_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    pinned_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    pinned_at: Mapped[datetime] = now_tz()


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------

class Call(Base):
    __tablename__ = "calls"
    __table_args__ = (
        CheckConstraint("type IN ('audio', 'video')", name="calls_type_check"),
        CheckConstraint("status IN ('ringing', 'answered', 'rejected', 'missed', 'ended')", name="calls_status_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False)
    caller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    callee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = now_tz()
    answered_at: Mapped[datetime | None] = dt_tz_nullable()
    ended_at: Mapped[datetime | None] = dt_tz_nullable()
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ---------------------------------------------------------------------------
# Poll votes
# ---------------------------------------------------------------------------

class PollVote(Base):
    __tablename__ = "poll_votes"

    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    option_indices: Mapped[str] = mapped_column(Text, nullable=False)
    voted_at: Mapped[datetime] = now_tz()

    message: Mapped["Message"] = relationship(back_populates="poll_votes")
