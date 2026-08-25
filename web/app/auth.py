from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .persistence.database import Database


LOGGER = logging.getLogger(__name__)
SESSION_COOKIE = "voice_lab_session"
SESSION_DAYS = 14
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{3,32}$")


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "status": user["status"],
        "must_change_password": bool(user.get("must_change_password")),
        "created_at": user["created_at"],
        "last_login_at": user.get("last_login_at"),
        "project_count": int(user.get("project_count") or 0),
    }


class AuthError(ValueError):
    pass


class AuthService:
    def __init__(self, database: Database, data_root: Path) -> None:
        self._database = database
        self._data_root = data_root
        self.bootstrap_password: str | None = None

    def ensure_bootstrap_admin(self) -> dict[str, Any]:
        existing = self._database.auth.get_by_username("admin")
        if existing:
            return existing
        configured = os.getenv("VOICE_LAB_ADMIN_PASSWORD", "").strip()
        password = configured or os.getenv("VOICE_LAB_ADMIN_TOKEN", "").strip()
        generated = not password
        password = password or secrets.token_urlsafe(16)
        self.bootstrap_password = password
        admin = self._database.auth.create_user(
            "admin",
            "系统管理员",
            hash_password(password),
            role="system_admin",
            must_change_password=generated,
        )
        if generated:
            hint = self._data_root / "bootstrap-admin.txt"
            hint.write_text(
                "Voice Lab 初始管理员\n" f"用户名: admin\n密码: {password}\n" "首次登录后请立即修改密码。\n",
                encoding="utf-8",
            )
            LOGGER.warning("已生成初始管理员凭据，请查看 %s", hint)
        return admin

    def authenticate(self, username: str, password: str) -> dict[str, Any]:
        user = self._database.auth.get_by_username(username)
        if (
            not user
            or user["status"] != "active"
            or not verify_password(password, str(user["password_hash"]))
        ):
            raise AuthError("用户名或密码不正确")
        self._database.auth.note_login(str(user["id"]))
        return self._database.auth.get_user(str(user["id"])) or user

    def create_session(self, user: dict[str, Any], ip: str, user_agent: str) -> str:
        token = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(days=SESSION_DAYS)
        self._database.auth.create_session(
            token_hash=token_hash(token),
            user_id=str(user["id"]),
            expires_at=expires.isoformat(timespec="microseconds"),
            ip_address=ip,
            user_agent=user_agent,
        )
        return token

    def user_for_token(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        return self._database.auth.user_for_session(token_hash(token))

    def logout(self, token: str) -> None:
        if token:
            self._database.auth.delete_session(token_hash(token))

    def create_user(
        self,
        username: str,
        display_name: str,
        password: str,
    ) -> tuple[dict[str, Any], str]:
        username = username.strip()
        display_name = display_name.strip()
        if not _USERNAME_RE.fullmatch(username):
            raise AuthError("用户名需为 3-32 位字母、数字、@、点、下划线或短横线")
        if not display_name or len(display_name) > 60:
            raise AuthError("显示名称需为 1-60 个字符")
        validate_password(password)
        if self._database.auth.get_by_username(username):
            raise AuthError("用户名已经存在")
        try:
            user = self._database.auth.create_user(
                username,
                display_name,
                hash_password(password),
                must_change_password=True,
            )
        except sqlite3.IntegrityError as error:
            raise AuthError("用户名已经存在") from error
        return user, password

    def change_password(self, user: dict[str, Any], old: str, new: str) -> None:
        if not verify_password(old, str(user["password_hash"])):
            raise AuthError("当前密码不正确")
        validate_password(new)
        if not self._database.auth.update_password(
            str(user["id"]),
            hash_password(new),
            must_change=False,
            invalidate_sessions=False,
        ):
            raise AuthError("账户不存在")
        if str(user.get("role")) == "system_admin":
            (self._data_root / "bootstrap-admin.txt").unlink(missing_ok=True)

    def reset_password(self, user_id: str, password: str) -> None:
        validate_password(password)
        if not self._database.auth.update_password(
            user_id, hash_password(password), must_change=True
        ):
            raise AuthError("账户不存在")


def validate_password(value: str) -> None:
    if not value or len(value) > 128:
        raise AuthError("密码不能为空且不能超过 128 个字符")


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        salt.hex(),
        digest.hex(),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = encoded.split("$", 5)
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
