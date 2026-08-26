from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.login_protection import LoginRateLimitError, LoginRateLimiter
from app.main import create_app
from conftest import FakeEngine


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_login_limiter_applies_exponential_delay_and_resets_on_success() -> None:
    clock = _Clock()
    limiter = LoginRateLimiter(clock=clock)

    for _ in range(5):
        limiter.check("127.0.0.1", "Admin")
        limiter.record_failure("127.0.0.1", "Admin")

    with pytest.raises(LoginRateLimitError) as first_block:
        limiter.check("127.0.0.1", "admin")
    assert first_block.value.retry_after == 1
    with pytest.raises(LoginRateLimitError):
        limiter.check("192.0.2.10", "admin")
    with pytest.raises(LoginRateLimitError):
        limiter.check("127.0.0.1", "another-user")

    clock.advance(1)
    limiter.check("127.0.0.1", "admin")
    limiter.record_failure("127.0.0.1", "admin")
    with pytest.raises(LoginRateLimitError) as second_block:
        limiter.check("127.0.0.1", "admin")
    assert second_block.value.retry_after == 2

    limiter.record_success("127.0.0.1", "admin")
    limiter.check("127.0.0.1", "admin")


def test_login_endpoint_returns_retry_after_without_rechecking_password(
    settings_factory,
) -> None:
    application = create_app(
        settings_factory(), engine_factory=FakeEngine, seed_legacy=False
    )

    with TestClient(application) as client:
        for _ in range(5):
            response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "incorrect-password"},
            )
            assert response.status_code == 401

        blocked = client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": application.state.services.auth.bootstrap_password,
            },
        )

        assert blocked.status_code == 429
        assert blocked.headers["retry-after"] == "1"
        assert blocked.json()["detail"] == "登录尝试过于频繁，请稍后再试"
        actions = {
            item["action"] for item in application.state.services.database.audit.list()
        }
        assert "auth.login_throttled" in actions
