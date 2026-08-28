from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

from ..access import AdminAccess
from ..audit import record_action
from ..dependencies import ServicesDep
from ..schemas import ElevenLabsProviderUpdate, MiniMaxProviderUpdate


router = APIRouter(prefix="/api/admin/providers")
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
LANGUAGE_BOOST_RE = re.compile(r"^[A-Za-z][A-Za-z, ]{0,31}$")


@router.get("")
def providers(services: ServicesDep, _: AdminAccess) -> dict[str, Any]:
    return _public_config(services)


@router.patch("/elevenlabs")
def update_elevenlabs(
    changes: ElevenLabsProviderUpdate,
    services: ServicesDep,
    admin: AdminAccess,
    request: Request,
) -> dict[str, Any]:
    base_url = _validated_base_url(changes.base_url)
    model_id = changes.tts_model_id.strip()
    if not MODEL_ID_RE.fullmatch(model_id):
        raise HTTPException(status_code=422, detail="模型 ID 只能包含字母、数字、点、下划线和短横线")
    try:
        current = services.provider_config.provider("elevenlabs")
    except RuntimeError:
        current = {}
    supplied_key = (changes.api_key or "").strip()
    effective_key = (
        ""
        if changes.clear_api_key
        else supplied_key or str(current.get("api_key") or "").strip()
    )
    if changes.enabled and not effective_key:
        raise HTTPException(status_code=422, detail="启用 ElevenLabs 前必须配置 API Key")
    payload = changes.model_dump(
        exclude={"api_key", "clear_api_key"},
    )
    payload.update(
        {
            "base_url": base_url,
            "tts_model_id": model_id,
            "api_key": effective_key,
        }
    )
    try:
        services.provider_config.update_provider("elevenlabs", payload)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="provider 配置不可用") from error
    record_action(
        services,
        request,
        admin,
        "admin.provider_updated",
        target_type="provider",
        target_id="elevenlabs",
        details={"enabled": changes.enabled, "model_id": model_id},
    )
    return _public_config(services)


@router.post("/elevenlabs/test")
def test_elevenlabs(
    services: ServicesDep, admin: AdminAccess, request: Request
) -> dict[str, Any]:
    try:
        result = services.engine.test_provider("elevenlabs")
    except (RuntimeError, ValueError) as error:
        record_action(
            services,
            request,
            admin,
            "admin.provider_tested",
            target_type="provider",
            target_id="elevenlabs",
            success=False,
        )
        raise HTTPException(status_code=502, detail=str(error)) from error
    record_action(
        services,
        request,
        admin,
        "admin.provider_tested",
        target_type="provider",
        target_id="elevenlabs",
    )
    return result


@router.patch("/minimax")
def update_minimax(
    changes: MiniMaxProviderUpdate,
    services: ServicesDep,
    admin: AdminAccess,
    request: Request,
) -> dict[str, Any]:
    base_url = _validated_base_url(changes.base_url)
    model_id = changes.tts_model_id.strip()
    language_boost = changes.language_boost.strip()
    if not MODEL_ID_RE.fullmatch(model_id):
        raise HTTPException(
            status_code=422,
            detail="模型 ID 只能包含字母、数字、点、下划线和短横线",
        )
    if not LANGUAGE_BOOST_RE.fullmatch(language_boost):
        raise HTTPException(status_code=422, detail="MiniMax 语言增强参数无效")
    try:
        current = services.provider_config.provider("minimax")
    except RuntimeError:
        current = {}
    supplied_key = (changes.api_key or "").strip()
    effective_key = (
        ""
        if changes.clear_api_key
        else supplied_key or str(current.get("api_key") or "").strip()
    )
    if changes.enabled and not effective_key:
        raise HTTPException(status_code=422, detail="启用 MiniMax 前必须配置 API Key")
    payload = changes.model_dump(exclude={"api_key", "clear_api_key"})
    payload.update(
        {
            "base_url": base_url,
            "tts_model_id": model_id,
            "language_boost": language_boost,
            "api_key": effective_key,
        }
    )
    try:
        services.provider_config.update_provider("minimax", payload)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="provider 配置不可用") from error
    record_action(
        services,
        request,
        admin,
        "admin.provider_updated",
        target_type="provider",
        target_id="minimax",
        details={"enabled": changes.enabled, "model_id": model_id},
    )
    return _public_config(services)


@router.post("/minimax/test")
def test_minimax(
    services: ServicesDep, admin: AdminAccess, request: Request
) -> dict[str, Any]:
    try:
        result = services.engine.test_provider("minimax")
    except (RuntimeError, ValueError) as error:
        record_action(
            services,
            request,
            admin,
            "admin.provider_tested",
            target_type="provider",
            target_id="minimax",
            success=False,
        )
        raise HTTPException(status_code=502, detail=str(error)) from error
    record_action(
        services,
        request,
        admin,
        "admin.provider_tested",
        target_type="provider",
        target_id="minimax",
    )
    return result


def _validated_base_url(value: str) -> str:
    cleaned = value.strip().rstrip("/")
    try:
        parsed = urlsplit(cleaned)
        hostname = parsed.hostname
        parsed.port
    except ValueError as error:
        raise HTTPException(status_code=422, detail="API 地址无效") from error
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(status_code=422, detail="API 地址不能包含凭据、查询参数或片段")
    if parsed.path not in {"", "/"}:
        raise HTTPException(status_code=422, detail="API 地址不能包含额外路径")
    loopback = hostname in {"127.0.0.1", "::1", "localhost"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise HTTPException(status_code=422, detail="API 地址必须使用 HTTPS；本机代理可使用 HTTP")
    if not hostname:
        raise HTTPException(status_code=422, detail="API 地址无效")
    return cleaned


def _public_config(services: ServicesDep) -> dict[str, Any]:
    try:
        return services.provider_config.public()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="provider 配置不可用") from error
