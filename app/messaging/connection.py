"""
WebSocket connection manager — multi-session edition.

Up to MAX_SESSIONS concurrent sessions per user. Each session gets a UUID.
Fan-out sends to all active sessions for a user.
Oldest session is evicted when the cap is hit.
"""

import logging
from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import WebSocket

logger = logging.getLogger(__name__)

MAX_SESSIONS = 5


class SessionEntry:
    def __init__(self, session_id: UUID, websocket: WebSocket, device_name: str | None):
        self.session_id = session_id
        self.websocket = websocket
        self.device_name = device_name
        self.connected_at = datetime.now(UTC)


class ConnectionManager:
    def __init__(self):
        self._sessions: dict[UUID, list[SessionEntry]] = defaultdict(list)

    async def connect(
        self,
        user_id: UUID,
        websocket: WebSocket,
        session_id: UUID,
        device_name: str | None = None,
    ) -> None:
        await websocket.accept()
        sessions = self._sessions[user_id]

        while len(sessions) >= MAX_SESSIONS:
            oldest = sessions.pop(0)
            try:
                await oldest.websocket.close(code=4003, reason="Session limit reached")
            except Exception:
                pass
            logger.info("Evicted oldest session %s for user %s", oldest.session_id, user_id)

        sessions.append(SessionEntry(session_id, websocket, device_name))
        logger.info("User %s connected session %s (%d active)", user_id, session_id, len(sessions))

    def disconnect(self, user_id: UUID, session_id: UUID) -> None:
        sessions = self._sessions.get(user_id, [])
        self._sessions[user_id] = [s for s in sessions if s.session_id != session_id]
        if not self._sessions[user_id]:
            del self._sessions[user_id]

    async def disconnect_session(self, user_id: UUID, session_id: UUID) -> bool:
        """Forcibly close a specific session (used by revoke endpoint)."""
        for entry in self._sessions.get(user_id, []):
            if entry.session_id == session_id:
                try:
                    await entry.websocket.close(code=4004, reason="Session revoked")
                except Exception:
                    pass
                self.disconnect(user_id, session_id)
                return True
        return False

    def is_online(self, user_id: UUID) -> bool:
        return bool(self._sessions.get(user_id))

    async def send(self, user_id: UUID, event: dict) -> bool:
        """Send to all sessions for a user. Returns True if at least one delivered."""
        sessions = self._sessions.get(user_id, [])
        if not sessions:
            return False

        delivered = False
        dead: list[UUID] = []

        for entry in sessions:
            try:
                await entry.websocket.send_json(event)
                delivered = True
            except Exception:
                dead.append(entry.session_id)

        for sid in dead:
            self.disconnect(user_id, sid)

        return delivered

    def online_user_ids(self) -> list[UUID]:
        return list(self._sessions.keys())

    def get_sessions(self, user_id: UUID) -> list[SessionEntry]:
        return list(self._sessions.get(user_id, []))


manager = ConnectionManager()