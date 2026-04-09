"""
Background cleanup job.

Runs nightly at 02:00 UTC.
Deletes messages past their retention period to keep DB and Spaces usage low.

Retention rules (configurable via env):
  - Delivered messages:   delete after DELIVERED_MESSAGE_TTL_DAYS  (default 7)
  - Undelivered messages: delete after UNDELIVERED_MESSAGE_TTL_DAYS (default 30)

Media files in DO Spaces are also deleted when their message is deleted.
"""

import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.media.spaces import delete_file
from app.models import Message, Receipt, Status

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


@scheduler.scheduled_job("cron", hour=2, minute=0)
async def cleanup_old_messages():
    logger.info("Running nightly message cleanup...")
    now = datetime.now(UTC)
    deleted_count = 0

    async with AsyncSessionLocal() as db:
        # 1. Delivered messages older than TTL
        delivered_cutoff = now - timedelta(days=settings.delivered_message_ttl_days)
        result = await db.execute(
            select(Message).where(
                Message.created_at < delivered_cutoff,
                Message.id.in_(
                    select(Receipt.message_id).where(Receipt.delivered_at.isnot(None))
                ),
            )
        )
        delivered_old = result.scalars().all()

        # 2. Undelivered messages older than TTL
        undelivered_cutoff = now - timedelta(days=settings.undelivered_message_ttl_days)
        result = await db.execute(
            select(Message).where(
                Message.created_at < undelivered_cutoff,
                Message.id.not_in(
                    select(Receipt.message_id).where(Receipt.delivered_at.isnot(None))
                ),
            )
        )
        undelivered_old = result.scalars().all()

        # 3. Messages with explicit expiry (disappearing messages)
        result = await db.execute(
            select(Message).where(
                Message.expires_at.isnot(None),
                Message.expires_at < now,
            )
        )
        expiring_old = result.scalars().all()

        # Deduplicate by id because a message can match TTL and explicit expiry.
        dedup: dict = {}
        for msg in delivered_old + undelivered_old + expiring_old:
            dedup[msg.id] = msg
        to_delete = list(dedup.values())

        for message in to_delete:
            # Delete media from Spaces if present
            if message.media_url:
                await delete_file(message.media_url)
            await db.delete(message)
            deleted_count += 1

        # 4. Expired statuses
        status_result = await db.execute(select(Status).where(Status.expires_at < now))
        expired_statuses = status_result.scalars().all()
        for status in expired_statuses:
            if status.media_url:
                await delete_file(status.media_url)
            await db.delete(status)
            deleted_count += 1

        await db.commit()

    logger.info("Cleanup complete. Deleted %d messages.", deleted_count)


def start_scheduler():
    if not scheduler.running:
        scheduler.start()
        logger.info("Cleanup scheduler started.")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()