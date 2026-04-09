from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service
from app.auth.dependencies import get_current_user
from app.auth.schemas import (
    RefreshIn,
    RequestOtpIn,
    RequestOtpOut,
    TokenPair,
    UpdateProfileIn,
    UserOut,
    VerifyOtpIn,
)
from app.database import get_db
from app.models import PushToken, User
from app.core.rate_limit import limiter

router = APIRouter()


class PushTokenIn(BaseModel):
    fcm_token: str


class VerifyOtpOut(BaseModel):
    """
    Returned by /verify-otp.

    Existing user:  needs_profile=False, tokens populated, setup_token=None
    New user:       needs_profile=True,  setup_token populated, tokens=None
    """
    needs_profile: bool
    access_token: str | None = None
    refresh_token: str | None = None
    setup_token: str | None = None


class CompleteProfileIn(BaseModel):
    setup_token: str
    display_name: str
    avatar_url: str | None = None  # pre-uploaded URL; None if user skipped avatar


@router.post("/request-otp", response_model=RequestOtpOut)
@limiter.limit("5/minute")
async def request_otp(
    request: Request,
    body: RequestOtpIn,
    db: AsyncSession = Depends(get_db),
):
    _ = request
    try:
        await service.request_otp(body.phone_number, db)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    return RequestOtpOut(message="OTP sent.")


@router.post("/verify-otp", response_model=VerifyOtpOut)
async def verify_otp(body: VerifyOtpIn, db: AsyncSession = Depends(get_db)):
    try:
        user, is_new, setup_token = await service.verify_otp_and_login(
            body.phone_number,
            body.code,
            db,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if is_new:
        # OTP verified — send the frontend to DisplayNameScreen
        return VerifyOtpOut(needs_profile=True, setup_token=setup_token)

    access, refresh = service.create_token_pair(str(user.id))
    return VerifyOtpOut(needs_profile=False, access_token=access, refresh_token=refresh)


@router.post("/complete-profile", response_model=TokenPair)
async def complete_profile(body: CompleteProfileIn, db: AsyncSession = Depends(get_db)):
    """
    Called by DisplayNameScreen once the user has entered their name (and optional avatar URL).
    Validates the setup token, creates the user, and returns a full token pair.
    """
    try:
        user = await service.complete_profile(
            body.setup_token,
            body.display_name,
            body.avatar_url,
            db,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    access, refresh = service.create_token_pair(str(user.id))
    return TokenPair(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenPair)
async def refresh_tokens(body: RefreshIn, db: AsyncSession = Depends(get_db)):
    try:
        user_id = service.decode_token(body.refresh_token, expected_kind="refresh")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")

    access, refresh = service.create_token_pair(str(user.id))
    return TokenPair(access_token=access, refresh_token=refresh)


@router.get("/users/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/users/me", response_model=UserOut)
async def update_me(
    body: UpdateProfileIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.display_name is not None:
        current_user.display_name = body.display_name.strip()
    if body.bio is not None:
        current_user.bio = body.bio.strip() or None
    if body.presence_visible is not None:
        current_user.presence_visible = body.presence_visible
    if body.receipts_visible is not None:
        current_user.receipts_visible = body.receipts_visible
    if body.show_timestamps is not None:
        current_user.show_timestamps = body.show_timestamps

    db.add(current_user)
    return current_user


@router.post("/users/me/push-token", status_code=status.HTTP_204_NO_CONTENT)
async def register_push_token(
    body: PushTokenIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Called by Android after login and whenever FCM issues a new token.
    Upserts the token — one token per user, last write wins.
    """
    token = await db.get(PushToken, current_user.id)
    if token:
        token.fcm_token = body.fcm_token
    else:
        token = PushToken(user_id=current_user.id, fcm_token=body.fcm_token)
        db.add(token)