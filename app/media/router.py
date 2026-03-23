"""
Media router.

POST /media/upload         — upload any media file, returns URL
POST /media/avatar         — upload avatar specifically, updates user profile
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.media.spaces import ALLOWED_TYPES, upload_file
from app.models import User

router = APIRouter()


class UploadOut(BaseModel):
    url: str
    mime_type: str


@router.post("/media/upload", response_model=UploadOut)
async def upload_media(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
):
    mime_type = file.content_type or ""

    if mime_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {mime_type}. Allowed: {', '.join(ALLOWED_TYPES.keys())}",
        )

    folder = ALLOWED_TYPES[mime_type]
    file_bytes = await file.read()

    try:
        url = await upload_file(file_bytes, mime_type, folder)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return UploadOut(url=url, mime_type=mime_type)


@router.post("/media/avatar", response_model=UploadOut)
async def upload_avatar(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    mime_type = file.content_type or ""

    if mime_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Avatar must be JPEG, PNG, or WebP.",
        )

    file_bytes = await file.read()

    try:
        url = await upload_file(file_bytes, mime_type, "avatars")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Update user profile
    current_user.avatar_url = url
    db.add(current_user)

    return UploadOut(url=url, mime_type=mime_type)