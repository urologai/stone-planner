"""Shared-password login and signed session cookies.

One password for the deployment (APP_PASSWORD), checked in constant time.
A successful login sets an HttpOnly, SameSite=Strict cookie holding a signed
random session ID; cases are owned by that ID. Failed logins are limited per
client address so the password cannot be guessed at speed.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

COOKIE_NAME = "sp_session"
MAX_FAILURES = 10
FAILURE_WINDOW_S = 15 * 60


class Auth:
    def __init__(self, secret_key: str, password: str, session_hours: int):
        self._serializer = URLSafeTimedSerializer(secret_key, salt="stoneplanner-session")
        self._password = password
        self.max_age_s = session_hours * 3600
        self._failures: dict[str, deque] = defaultdict(deque)

    @property
    def configured(self) -> bool:
        return bool(self._password)

    @staticmethod
    def client_key(request: Request) -> str:
        # Render's proxy puts the caller first in X-Forwarded-For.
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _prune(self, key: str) -> deque:
        failures = self._failures[key]
        cutoff = time.monotonic() - FAILURE_WINDOW_S
        while failures and failures[0] < cutoff:
            failures.popleft()
        return failures

    def check_password(self, request: Request, password: str) -> str:
        """Return a new signed session token, or raise 401 / 429."""
        key = self.client_key(request)
        failures = self._prune(key)
        if len(failures) >= MAX_FAILURES:
            raise HTTPException(429, "Too many failed logins; try again later.")
        if not self._password or not hmac.compare_digest(
                password.encode("utf-8"), self._password.encode("utf-8")):
            failures.append(time.monotonic())
            raise HTTPException(401, "Wrong password.")
        failures.clear()
        return self._serializer.dumps({"sid": secrets.token_urlsafe(16)})

    def session_id(self, request: Request) -> str | None:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None
        try:
            data = self._serializer.loads(token, max_age=self.max_age_s)
        except BadSignature:
            return None
        return data.get("sid") if isinstance(data, dict) else None

    def require(self, request: Request) -> str:
        sid = self.session_id(request)
        if sid is None:
            raise HTTPException(401, "Log in first.")
        return sid
