"""
Auth service.

OTP flow:
  1. request-otp      → generate 6-digit code, hash it, store in otp_codes, send via Africa's Talking
  2. verify-otp       → look up unexpired unused code, bcrypt verify, mark used
                        if user exists  → issue tokens            (needs_profile: false)
                        if new user     → issue short-lived setup token (needs_profile: true)
  3. complete-profile → validate setup token, create user with display_name + optional avatar_url
                        → issue full token pair
  4. refresh          → validate refresh token, issue new pair

Rate limiting: max 3 active (unused, unexpired) OTPs per phone number.
This prevents SMS bombing without a Redis dependency.
"""

import hashlib
import random
import string
from datetime import UTC, datetime, timedelta

import httpx
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import OtpCode, User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_OTP_TTL_MINUTES = 10
_OTP_MAX_ACTIVE = 3

# A short-lived token kind used only between verify-otp and complete-profile.
# It carries the verified phone number but grants no access to the API.
_SETUP_TOKEN_TTL_MINUTES = 15


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------

def hash_otp(code: str) -> str:
    return pwd_context.hash(code)


def verify_otp(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_phone(phone_number: str) -> str:
    """Deterministic SHA-256 hash of the phone number for contact sync matching."""
    return hashlib.sha256(phone_number.encode()).hexdigest()


# ---------------------------------------------------------------------------
# OTP generation and delivery
# ---------------------------------------------------------------------------

def _generate_code() -> str:
    return "".join(random.choices(string.digits, k=6))


async def _send_sms(phone_number: str, code: str) -> None:
    """
    Send OTP via Tiara Connect.
    Phone number must be in E.164 without the +, e.g. 254712345678
    The + is stripped here since Tiara expects digits only.
    """
    to = phone_number.lstrip("+")

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            settings.tiara_endpoint,
            headers={
                "Authorization": f"Bearer {settings.tiara_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.tiara_sender_id,
                "to": to,
                "message": f"Your Letta code is {code}. Valid for {_OTP_TTL_MINUTES} minutes.",
            },
        )

    data = response.json()
    print(f"[Tiara] status={response.status_code} body={data}")

    if response.status_code != 200 or data.get("status") != "SUCCESS":
        raise RuntimeError(f"Tiara Connect error: {data.get('desc', response.text)}")


async def request_otp(phone_number: str, db: AsyncSession) -> None:
    """Generate and send an OTP. Enforces rate limit."""
    now = datetime.now(UTC)

    # Count active OTPs for this number
    result = await db.execute(
        select(OtpCode).where(
            OtpCode.phone_number == phone_number,
            OtpCode.used.is_(False),
            OtpCode.expires_at > now,
        )
    )
    active = result.scalars().all()
    if len(active) >= _OTP_MAX_ACTIVE:
        raise ValueError("Too many OTP requests. Please wait before requesting another.")

    code = _generate_code()
    otp = OtpCode(
        phone_number=phone_number,
        code_hash=hash_otp(code),
        expires_at=now + timedelta(minutes=_OTP_TTL_MINUTES),
    )
    db.add(otp)
    await db.flush()  # get the id without committing

    await _send_sms(phone_number, code)
    # commit happens in get_db() on successful response


# ---------------------------------------------------------------------------
# OTP verification and user resolution
# ---------------------------------------------------------------------------

async def verify_otp_and_login(
    phone_number: str,
    code: str,
    db: AsyncSession,
) -> tuple[User | None, bool, str | None]:
    """
    Verify OTP. Returns (user, is_new_user, setup_token).

    Existing user → (user,  False, None)         caller issues full token pair
    New user      → (None,  True,  setup_token)   caller returns needs_profile response;
                                                   setup_token is passed to complete-profile

    Raises ValueError for any invalid/expired/used code.
    """
    now = datetime.now(UTC)

    result = await db.execute(
        select(OtpCode).where(
            OtpCode.phone_number == phone_number,
            OtpCode.used.is_(False),
            OtpCode.expires_at > now,
        ).order_by(OtpCode.expires_at.desc())
    )
    candidates = result.scalars().all()

    matched: OtpCode | None = None
    for candidate in candidates:
        if verify_otp(code, candidate.code_hash):
            matched = candidate
            break

    if not matched:
        raise ValueError("Invalid or expired code.")

    matched.used = True
    await db.flush()

    # Look up existing user
    result = await db.execute(
        select(User).where(User.phone_number == phone_number)
    )
    user = result.scalar_one_or_none()

    if user:
        user.last_seen = now
        return user, False, None

    # New user — OTP is verified but we still need their display name (and optional avatar).
    # Issue a short-lived setup token so the frontend can POST to /auth/complete-profile
    # without re-verifying the OTP.
    setup_token = _make_token(
        sub=phone_number,
        kind="setup",
        expires_delta=timedelta(minutes=_SETUP_TOKEN_TTL_MINUTES),
    )
    return None, True, setup_token


async def complete_profile(
    setup_token: str,
    display_name: str,
    avatar_url: str | None,
    db: AsyncSession,
) -> User:
    """
    Validate a setup token and create the new user account.

    Called after DisplayNameScreen collects display_name (and optional avatar).
    Returns the newly created User so the caller can issue a full token pair.
    """
    phone_number = decode_token(setup_token, expected_kind="setup")

    # Guard: don't create a duplicate if somehow called twice
    result = await db.execute(
        select(User).where(User.phone_number == phone_number)
    )
    existing = result.scalar_one_or_none()
    if existing:
        # Already created (e.g. double-tap). Return as-is.
        return existing

    stripped = display_name.strip()
    if len(stripped) < 2:
        raise ValueError("display_name must be at least 2 characters.")

    user = User(
        phone_number=phone_number,
        phone_hash=hash_phone(phone_number),
        display_name=stripped,
        avatar_url=avatar_url,
    )
    db.add(user)
    await db.flush()
    return user


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def _make_token(sub: str, kind: str, expires_delta: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "kind": kind,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_token_pair(user_id: str) -> tuple[str, str]:
    access = _make_token(
        user_id, "access",
        timedelta(minutes=settings.access_token_expire_minutes),
    )
    refresh = _make_token(
        user_id, "refresh",
        timedelta(days=settings.refresh_token_expire_days),
    )
    return access, refresh


def decode_token(token: str, expected_kind: str = "access") -> str:
    """Decode and validate a JWT. Returns the sub claim. Raises on failure."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as e:
        raise ValueError(f"Invalid token: {e}") from e

    if payload.get("kind") != expected_kind:
        raise ValueError(f"Expected {expected_kind} token, got {payload.get('kind')}")

    sub = payload.get("sub")
    if not sub:
        raise ValueError("Token missing subject.")

    return sub