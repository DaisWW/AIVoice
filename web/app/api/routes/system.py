from typing import Any

from fastapi import APIRouter

from ...auth import public_user
from ...audio_conversion import AUDIO_FORMAT_LABEL, SUPPORTED_AUDIO_EXTENSIONS
from ...generation_settings import (
    generation_defaults,
    public_generation_controls,
)
from ...reference_emotions import public_reference_emotions
from ...script_parser import SUPPORTED_SCRIPT_EXTENSIONS
from ..dependencies import CurrentUser, ServicesDep


router = APIRouter(prefix="/api")


@router.get("/healthz", include_in_schema=False)
def liveness() -> dict[str, bool]:
    """Unauthenticated, detail-free endpoint for container health checks."""
    return {"ok": True}


@router.get("/identity")
def identity(user: CurrentUser) -> dict[str, Any]:
    return {"user": public_user(user)}


@router.get("/health")
def health(_: CurrentUser, services: ServicesDep) -> dict[str, Any]:
    return {
        "ok": True,
        "queue": {
            **services.database.monitoring.counts(),
            **services.job_queue.status(),
        },
        "engine": services.engine.model_status(),
    }


@router.get("/config")
def config(_: CurrentUser, services: ServicesDep) -> dict[str, Any]:
    engine_status = services.engine.model_status()
    return {
        **services.profiles.public(engine_status.get("models", {})),
        "script_extensions": sorted(SUPPORTED_SCRIPT_EXTENSIONS),
        "audio_extensions": sorted(SUPPORTED_AUDIO_EXTENSIONS),
        "reference_emotions": public_reference_emotions(),
        "generation_controls": public_generation_controls(),
        "generation_defaults": generation_defaults(),
        "voice_requirements": f"真人模板支持 {AUDIO_FORMAT_LABEL}，上传后自动转为单声道 48 kHz WAV。建议无背景音乐、每条 3-15 秒，至少上传 2 条。",
        "output_description": "直接输出所选声音克隆模型原音，不做降噪、变调、EQ、压缩、混响或响度处理。",
        "text_model": services.text_generation.public(),
        "script_format": {
            "txt_markdown": "角色标签 | mo——la，na？↗",
            "tab": "角色标签<TAB>mo——la，na？↗",
            "csv": "text,pronunciation\\n角色标签,mo——la，na？↗",
            "raw": "虫语角色 | raw: t͡ʃa-ʀ——ɬa↗",
            "legacy": "纯发音行也兼容；raw:/ipa:/phoneme: 前缀可保留自定义音素，不映射成汉字。",
            "hold": "- 只分隔音节；— 会保留在发音标记中供外部后期参考，本工具不执行延音。",
        },
    }
