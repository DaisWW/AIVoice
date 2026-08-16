from __future__ import annotations

from typing import Any

from fastapi import Request

from ..services import ApplicationServices


def record_action(
    services: ApplicationServices,
    request: Request,
    user: dict[str, Any],
    action: str,
    *,
    target_type: str = "",
    target_id: str = "",
    project_id: str | None = None,
    details: dict[str, Any] | None = None,
    success: bool = True,
) -> None:
    services.database.audit.record(
        action,
        actor=user,
        target_type=target_type,
        target_id=target_id,
        project_id=project_id,
        ip_address=request.client.host if request.client else "",
        details=details,
        success=success,
    )
