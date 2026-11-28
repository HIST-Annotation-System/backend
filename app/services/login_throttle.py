"""Slows down password guessing: after too many failed sign-ins to one username, that username is refused for a while.

Kept in memory, which is enough because the API runs as a single process (see docker-compose.prod.yml). Counted per
username, not per client address: behind CloudFront every request comes from a CloudFront address, so per-address
counting would throttle everyone at once. The cost is that someone who knows a username can keep its owner from
signing in, for at most `window` at a time.
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable

# Failed attempts allowed per username within the window; the next one is refused until the oldest leaves the window.
LOGIN_MAX_FAILURES = 10
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
# Usernames tracked at once: past this, those whose failures have all left the window are forgotten.
LOGIN_THROTTLE_MAX_KEYS = 10_000


class LoginThrottle:
    def __init__(
        self,
        max_failures: int = LOGIN_MAX_FAILURES,
        window_seconds: float = LOGIN_FAILURE_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_failures = max_failures
        self._window = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(username: str) -> str:
        return username.strip().casefold()

    def _recent(self, key: str, now: float) -> deque[float] | None:
        failures = self._failures.get(key)
        if failures is None:
            return None
        while failures and failures[0] <= now - self._window:
            failures.popleft()
        if not failures:
            del self._failures[key]
            return None
        return failures

    def retry_after(self, username: str) -> int | None:
        """Seconds until the username may try again, or None if it may try now."""
        with self._lock:
            now = self._clock()
            failures = self._recent(self._key(username), now)
            if failures is None or len(failures) < self._max_failures:
                return None
            return max(1, math.ceil(failures[0] + self._window - now))

    def record_failure(self, username: str) -> None:
        with self._lock:
            now = self._clock()
            key = self._key(username)
            failures = self._recent(key, now)
            if failures is None:
                if len(self._failures) >= LOGIN_THROTTLE_MAX_KEYS:
                    self._forget_stale(now)
                failures = self._failures.setdefault(key, deque())
            failures.append(now)

    def record_success(self, username: str) -> None:
        with self._lock:
            self._failures.pop(self._key(username), None)

    def _forget_stale(self, now: float) -> None:
        for key in list(self._failures):
            self._recent(key, now)
