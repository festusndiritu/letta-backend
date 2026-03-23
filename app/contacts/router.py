from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.contacts import service
from app.contacts.schemas import BlockIn, ContactSyncIn, ContactSyncOut, ContactOut
from app.database import get_db
from app.models import User

router = APIRouter()


@router.post("/contacts/sync", response_model=ContactSyncOut)
async def sync_contacts(
    body: ContactSyncIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    matched = await service.sync_contacts(current_user, body.phone_hashes, db)
    return ContactSyncOut(contacts=[ContactOut(**c) for c in matched])


@router.post("/contacts/block", status_code=status.HTTP_204_NO_CONTENT)
async def block_user(
    body: BlockIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.user_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot block yourself.")
    await service.block_user(current_user, body.user_id, db)


@router.post("/contacts/unblock", status_code=status.HTTP_204_NO_CONTENT)
async def unblock_user(
    body: BlockIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.unblock_user(current_user, body.user_id, db)