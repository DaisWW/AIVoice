from typing import Any

from fastapi import APIRouter, Request

from ...audio_conversion import AUDIO_FORMAT_LABEL, SUPPORTED_AUDIO_EXTENSIONS
from ...generation_settings import (
    generation_defaults,
    public_generation_controls,
)
from ...reference_emotions import public_reference_emotions
from ...script_parser import SUPPORTED_SCRIPT_EXTENSIONS
from ..access import is_local_admin
from ..dependencies import ClientId, ServicesDep


router = APIRouter(prefix="/api")


@router.get("/identity")
def identity(request: Request, client_id: ClientId) -> dict[str, Any]:
    return {"client_id": client_id, "admin_available": is_local_admin(request)}


@router.get("/health")
def health(services: ServicesDep) -> dict[str, Any]:
    return {
        "ok": True,
        "queue": {
            **services.database.monitoring.counts(),
            **services.job_queue.status(),
        },
        "engine": services.engine.model_status(),
    }


@router.get("/config")
def config(services: ServicesDep) -> dict[str, Any]:
    return {
        **services.profiles.public(),
        "script_extensions": sorted(SUPPORTED_SCRIPT_EXTENSIONS),
        "audio_extensions": sorted(SUPPORTED_AUDIO_EXTENSIONS),
        "reference_emotions": public_reference_emotions(),
        "generation_controls": public_generation_controls(),
        "generation_defaults": generation_defaults(),
        "voice_requirements": f"真人模板支持 {AUDIO_FORMAT_LABEL}，上传后自动转为单声道 48 kHz WAV。建议无背景音乐、每条 3-15 秒，至少上传 2 条。",
        "output_description": "直接输出 GPT-SoVITS V2 克隆原音，不做降噪、变调、EQ、压缩、混响或响度处理。",
        "script_format": {
            "txt_markdown": "正常中文台词 | mo——la，na？↗",
            "tab": "正常中文台词<TAB>mo——la，na？↗",
            "csv": "text,pronunciation\\n陌生人，可否听我讲一段故事,mo——la，na？↗",
            "legacy": "纯发音行也兼容，但结果页会以生成汉字作为正常台词。",
            "hold": "- 只分隔音节；— 会保留在发音标记中供外部后期参考，本工具不执行延音。",
        },
    }
