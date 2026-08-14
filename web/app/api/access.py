from __future__ import annotations

import hmac
import os
import socket
from functools import lru_cache
from ipaddress import ip_address
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Response

from .dependencies import ServicesDep


LOCAL_ADMIN_HOSTS = frozenset({"127.0.0.1", "::1"})
ADMIN_COOKIE = "voice_lab_admin"
ADMIN_QUERY = "admin_key"
ADMIN_COOKIE_MAX_AGE = 30 * 24 * 60 * 60


@lru_cache(maxsize=1)
def _local_admin_hosts() -> frozenset[str]:
    hosts = set(LOCAL_ADMIN_HOSTS)
    try:
        addresses = socket.getaddrinfo(
            socket.gethostname(), None, type=socket.SOCK_STREAM
        )
    except OSError:
        return frozenset(hosts)
    for address in addresses:
        host = _normalize_host(address[4][0])
        if host:
            hosts.add(host)
    return frozenset(hosts)


def _normalize_host(host: str) -> str | None:
    candidate = host.split("%", 1)[0]
    try:
        address = ip_address(candidate)
    except ValueError:
        return None
    if address.is_unspecified:
        return None
    if address.version == 6 and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return str(address)


def is_local_admin(request: Request) -> bool:
    host = _normalize_host(request.client.host) if request.client else None
    if host and host in _local_admin_hosts():
        return True
    return _has_admin_token(request)


def require_local_admin(request: Request) -> None:
    if not is_local_admin(request):
        raise HTTPException(status_code=403, detail="管理员界面仅允许在服务器本机访问")


def set_admin_cookie_if_bootstrapped(request: Request, response: Response) -> bool:
    """Turn the one-time local Docker URL key into a browser session cookie."""
    token = _configured_admin_token()
    if not token or request.url.path.rstrip("/") != "/admin":
        return False
    if not _secure_equal(request.query_params.get(ADMIN_QUERY, ""), token):
        return False
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        max_age=ADMIN_COOKIE_MAX_AGE,
        samesite="lax",
        httponly=True,
        path="/",
    )
    return True


def _has_admin_token(request: Request) -> bool:
    token = _configured_admin_token()
    if not token:
        return False
    cookie = request.cookies.get(ADMIN_COOKIE, "")
    if _secure_equal(cookie, token):
        return True
    if request.url.path.rstrip("/") != "/admin":
        return False
    return _secure_equal(request.query_params.get(ADMIN_QUERY, ""), token)


def _configured_admin_token() -> str:
    return os.getenv("VOICE_LAB_ADMIN_TOKEN", "").strip()


def _secure_equal(left: str, right: str) -> bool:
    return bool(left and right) and hmac.compare_digest(left, right)


AdminAccess = Annotated[None, Depends(require_local_admin)]


def can_edit_voice(voice: dict[str, Any], client_id: str, request: Request) -> bool:
    return str(voice["owner_id"]) == client_id or is_local_admin(request)


def owned_job(services: ServicesDep, job_id: str, client_id: str) -> dict[str, Any]:
    job = services.database.jobs.get_for_client(job_id, client_id)
    if not job:
        raise HTTPException(status_code=404, detail="找不到任务")
    return job


def admin_job(services: ServicesDep, job_id: str) -> dict[str, Any]:
    job = services.database.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="找不到任务")
    return job
