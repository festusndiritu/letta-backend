"""
FCM v1 push notification sender.

Authentication: the Firebase service account JSON is stored as a single
environment variable (FCM_SERVICE_ACCOUNT_JSON) — paste the entire JSON
content as the value in Dokploy's env var UI. No file placement needed.

We send a notification payload alongside the knock data so the OS can
show a notification when the app is closed, while the Android app still
wakes up and opens its WebSocket to pull the actual message.
"""

import json
import logging

import httpx
from google.auth.transport.requests import Request  # type: ignore[import-untyped]
from google.oauth2 import service_account  # type: ignore[import-untyped]

from app.config import settings

logger = logging.getLogger(__name__)

_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_FCM_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

_credentials: service_account.Credentials | None = None


def _get_credentials() -> service_account.Credentials:
    global _credentials

    if _credentials is None:
        raw = settings.fcm_service_account_json
        if not raw:
            raise ValueError(
                "FCM_SERVICE_ACCOUNT_JSON is not set. "
                "Paste the Firebase service account JSON as this env var in Dokploy."
            )
        info = json.loads(raw)
        _credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=[_FCM_SCOPE],
        )

    if not _credentials.valid:
        _credentials.refresh(Request())

    return _credentials


def _get_access_token() -> str:
    return _get_credentials().token


async def send_knock(
    fcm_token: str,
    conversation_id: str,
    title: str,
    body: str,
) -> bool:
    """
    Send a notification + data (knock) push.

    The notification payload allows the OS to show a message when the app
    is closed. The data payload still carries the conversation_id so the
    Android app can sync content on open.
    Returns True on success, False on any error (non-fatal).
    """
    project_id = settings.fcm_project_id
    if not project_id:
        logger.warning("FCM_PROJECT_ID not set — skipping push notification")
        return False

    try:
        access_token = _get_access_token()
    except (ValueError, Exception) as e:
        logger.error("FCM credentials error: %s", e)
        return False

    url = _FCM_URL.format(project_id=project_id)
    payload = {
        "message": {
            "token": fcm_token,
            "notification": {
                "title": title,
                "body": body,
            },
            "data": {
                "type": "knock",
                "conversation_id": conversation_id,
            },
            "android": {
                "priority": "high",
            },
        }
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
            if response.status_code == 200:
                return True
            logger.warning("FCM push failed [%s]: %s", response.status_code, response.text)
            return False
        except httpx.RequestError as e:
            logger.error("FCM request error: %s", e)
            return False
