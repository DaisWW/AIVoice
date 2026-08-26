from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _AttemptState:
    failures: int
    last_failure: float
    blocked_until: float


class LoginRateLimitError(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("登录尝试过于频繁，请稍后再试")
        self.retry_after = retry_after


class LoginRateLimiter:
    """Bound repeated authentication failures without storing credentials."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        failure_window_seconds: int = 15 * 60,
        maximum_delay_seconds: int = 60,
        maximum_entries: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._failure_window_seconds = failure_window_seconds
        self._maximum_delay_seconds = maximum_delay_seconds
        self._maximum_entries = maximum_entries
        self._clock = clock
        self._attempts: OrderedDict[str, _AttemptState] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, ip_address: str, username: str) -> None:
        now = self._clock()
        with self._lock:
            self._discard_expired(now)
            blocked_until = max(
                (
                    state.blocked_until
                    for key in self._keys(ip_address, username)
                    if (state := self._attempts.get(key)) is not None
                ),
                default=0.0,
            )
        if blocked_until > now:
            raise LoginRateLimitError(math.ceil(blocked_until - now))

    def record_failure(self, ip_address: str, username: str) -> None:
        now = self._clock()
        with self._lock:
            self._discard_expired(now)
            for key in self._keys(ip_address, username):
                state = self._attempts.pop(key, None)
                failures = 1 if state is None else state.failures + 1
                delay = self._delay_seconds(failures)
                self._attempts[key] = _AttemptState(
                    failures=failures,
                    last_failure=now,
                    blocked_until=now + delay,
                )
            while len(self._attempts) > self._maximum_entries:
                self._attempts.popitem(last=False)

    def record_success(self, ip_address: str, username: str) -> None:
        with self._lock:
            for key in self._keys(ip_address, username):
                self._attempts.pop(key, None)

    def _discard_expired(self, now: float) -> None:
        expired = [
            key
            for key, state in self._attempts.items()
            if now - state.last_failure >= self._failure_window_seconds
        ]
        for key in expired:
            self._attempts.pop(key, None)

    def _delay_seconds(self, failures: int) -> int:
        if failures < self._failure_threshold:
            return 0
        exponent = failures - self._failure_threshold
        return min(2**exponent, self._maximum_delay_seconds)

    @staticmethod
    def _keys(ip_address: str, username: str) -> tuple[str, str]:
        ip_key = (ip_address or "unknown")[:128]
        username_key = username.strip().casefold()[:64]
        return f"ip:{ip_key}", f"username:{username_key}"
