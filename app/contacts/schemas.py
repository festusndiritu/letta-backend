import uuid

from pydantic import BaseModel


class ContactSyncIn(BaseModel):
    # Client sends SHA-256 hashes of all phone numbers in their address book
    phone_hashes: list[str]


class ContactOut(BaseModel):
    user_id: uuid.UUID
    display_name: str
    avatar_url: str | None
    phone_hash: str

    model_config = {"from_attributes": True}


class ContactSyncOut(BaseModel):
    # Returns only the contacts who are registered on Letta
    contacts: list[ContactOut]


class BlockIn(BaseModel):
    user_id: uuid.UUID