import uuid

from pydantic import BaseModel, field_validator


class RequestOtpIn(BaseModel):
    phone_number: str  # E.164 format expected: +254712345678

    @field_validator("phone_number")
    @classmethod
    def must_be_e164(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("+") or not v[1:].isdigit() or len(v) < 8:
            raise ValueError("Phone number must be in E.164 format e.g. +254712345678")
        return v


class RequestOtpOut(BaseModel):
    message: str


class VerifyOtpIn(BaseModel):
    phone_number: str
    code: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshIn(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: uuid.UUID
    phone_number: str
    display_name: str
    bio: str | None
    avatar_url: str | None
    presence_visible: bool
    receipts_visible: bool
    show_timestamps: bool

    model_config = {"from_attributes": True}


class UpdateProfileIn(BaseModel):
    display_name: str | None = None
    bio: str | None = None
    presence_visible: bool | None = None
    receipts_visible: bool | None = None
    show_timestamps: bool | None = None