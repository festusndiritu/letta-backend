"""
Auth service.

OTP flow:
  1. request-otp  → generate 6-digit code, hash it, store in otp_codes, send via Africa's Talking
  2. verify-otp   → look up unexpired unused code, bcrypt verify, mark used
                    if user exists → issue tokens
                    if new user   → require display_name, create user, issue tokens
  3. refresh      → validate refresh token, issue new pair

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
    async with httpx.AsyncClient() as client:
        data = {
            "username": settings.at_username,
            "to": phone_number,
            "message": f"Your Letta code is {code}. Valid for {_OTP_TTL_MINUTES} minutes.",
        }
        if settings.at_sender_id:
            data["from"] = settings.at_sender_id

        response = await client.post(
            "https://api.africastalking.com/version1/messaging",
            headers={
                "apiKey": settings.at_api_key,
                "Accept": "application/json",
            },
            data=data,
            timeout=10.0,
        )
        print(f"[AT] status={response.status_code} body={response.text}")
        if response.status_code != 201:
            raise RuntimeError(f"Africa's Talking error: {response.text}")


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
    display_name: str | None,
    db: AsyncSession,
) -> tuple[User, bool]:
    """
    Verify OTP. Returns (user, is_new_user).
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
        return user, False

    # New user — display_name required
    if not display_name or not display_name.strip():
        raise ValueError("display_name is required for new accounts.")

    user = User(
        phone_number=phone_number,
        phone_hash=hash_phone(phone_number),
        display_name=display_name.strip(),
    )
    db.add(user)
    await db.flush()
    return user, True


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
    """Decode and validate a JWT. Returns the user_id (sub). Raises on failure."""
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