from __future__ import annotations

import json
import secrets
from typing import Any

from fastapi import HTTPException

from ..domain import ScriptItem
from ..reference_emotions import validate_emotion
from ..services import ApplicationServices
from .payloads import JobPresenter
from .uploads import ScriptStorage


class JobCreationService:
    def __init__(
        self, services: ApplicationServices, user_id: str, project_id: str
    ) -> None:
        self._services = services
        self._user_id = user_id
        self._project_id = project_id

    def create(
        self,
        *,
        requested_voice_id: str,
        model_id: str,
        model_ids_json: str,
        script_id: str,
        name: str,
        candidate_count: int,
        reference_emotion: str,
        generation_settings_json: str,
        base_seed: int | None,
    ) -> list[dict[str, Any]]:
        emotion = self._emotion(reference_emotion)
        model_ids = self._model_ids(model_id, model_ids_json)
        self._validate_request(model_ids, name, candidate_count)
        primary_settings = self._settings(model_id, generation_settings_json)
        self._validate_seed(base_seed, 1)
        selected_script = self._script(script_id)
        configured_voice_id = self._configured_voice(
            selected_script,
            requested_voice_id,
        )
        self._validate_voice(configured_voice_id, emotion)
        items = ScriptStorage(self._services).load_items(selected_script)
        self._validate_seed(base_seed, len(items) * candidate_count)
        effective_seed = self._shared_seed(
            base_seed, len(model_ids), len(items) * candidate_count
        )
        jobs = self._create_jobs(
            str(selected_script["id"]),
            configured_voice_id,
            model_ids,
            items,
            name.strip() or str(selected_script["name"]),
            candidate_count,
            emotion,
            primary_settings,
            effective_seed,
        )
        for job in jobs:
            self._services.job_queue.submit(str(job["id"]))
        return jobs

    def _create_jobs(
        self,
        script_id: str,
        voice_id: str,
        model_ids: list[str],
        items: list[ScriptItem],
        base_name: str,
        candidate_count: int,
        emotion: str,
        primary_settings: dict[str, float | int],
        base_seed: int | None,
    ) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for index, model_id in enumerate(model_ids):
            settings = (
                primary_settings
                if index == 0
                else self._services.profiles.generation_settings(model_id)
            )
            job_id = self._services.database.jobs.create(
                self._user_id,
                script_id,
                voice_id,
                model_id,
                items,
                candidate_count=candidate_count,
                reference_emotion=emotion,
                generation_settings=settings,
                base_seed=base_seed,
                project_id=self._project_id,
                created_by=self._user_id,
            )
            self._services.database.jobs.rename(
                job_id,
                self._job_name(base_name, model_id, len(model_ids)),
                self._user_id,
            )
            job = self._services.database.jobs.get(job_id)
            if not job:  # pragma: no cover
                raise HTTPException(status_code=500, detail="任务创建后未找到")
            jobs.append(JobPresenter(self._services).payload(job))
        return jobs

    def _validate_request(
        self,
        model_ids: list[str],
        name: str,
        candidate_count: int,
    ) -> None:
        if len(name.strip()) > 80:
            raise HTTPException(status_code=422, detail="任务名称不能超过 80 个字符")
        if candidate_count not in {2, 3}:
            raise HTTPException(status_code=422, detail="每句候选数量只能选择 2 或 3")
        for model_id in model_ids:
            self._validate_model(model_id)

    def _validate_voice(self, voice_id: str, emotion: str) -> None:
        voice = self._services.database.voices.get(voice_id)
        if not voice or str(voice.get("project_id") or "") != self._project_id:
            raise HTTPException(status_code=404, detail="找不到台本配置的声音库")
        if int(voice.get("enabled_file_count") or 0) < 1:
            raise HTTPException(status_code=422, detail="台本配置的声音库没有启用的录音")
        if emotion == "all":
            return
        files = self._services.database.voices.list_files(voice_id)
        if not any(item.get("emotion_tag") == emotion for item in files):
            raise HTTPException(status_code=422, detail="台本配置的声音库没有启用该语气分组的录音")

    def _validate_model(self, model_id: str) -> None:
        try:
            self._services.profiles.model(model_id)
            status = self._services.engine.model_available(model_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if not status.available:
            raise HTTPException(
                status_code=422,
                detail=status.reason or "当前服务器尚未安装该模型",
            )

    def _script(self, script_id: str) -> dict[str, Any]:
        selected_id = script_id.strip()
        if not selected_id:
            raise HTTPException(status_code=422, detail="请选择台本库中的台本")
        script = self._services.database.scripts.get(selected_id)
        if not script or str(script.get("project_id") or "") != self._project_id:
            raise HTTPException(status_code=404, detail="找不到台本")
        return script

    @staticmethod
    def _configured_voice(script: dict[str, Any], requested_voice_id: str) -> str:
        configured = str(script.get("default_voice_id") or "").strip()
        if not configured:
            raise HTTPException(
                status_code=422,
                detail="台本尚未配置声音，请先到台本库完成配置",
            )
        requested = requested_voice_id.strip()
        if requested and requested != configured:
            raise HTTPException(
                status_code=422,
                detail="声音由台本库配置，不能在生成时覆盖",
            )
        return configured

    def _settings(self, model_id: str, value: str) -> dict[str, float | int]:
        try:
            requested = json.loads(value) if value.strip() else {}
            return self._services.profiles.resolve_generation_settings(
                model_id, requested
            )
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=f"生成参数错误: {error}") from error

    @staticmethod
    def _model_ids(primary: str, value: str) -> list[str]:
        try:
            parsed = json.loads(value) if value.strip() else []
        except json.JSONDecodeError as error:
            raise HTTPException(status_code=422, detail="对比模型格式错误") from error
        if not isinstance(parsed, list) or any(
            not isinstance(item, str) for item in parsed
        ):
            raise HTTPException(status_code=422, detail="对比模型必须是模型 ID 数组")
        result = list(dict.fromkeys([primary, *parsed]))
        if not primary or len(result) > 4:
            raise HTTPException(status_code=422, detail="每次请选择 1-4 个模型")
        return result

    @staticmethod
    def _emotion(value: str) -> str:
        try:
            return validate_emotion(value, allow_all=True)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @staticmethod
    def _validate_seed(value: int | None, required_seeds: int) -> None:
        if value is None:
            return
        maximum = 2_147_483_647 - max(0, required_seeds - 1)
        if value < 0 or value > maximum:
            raise HTTPException(
                status_code=422,
                detail=f"基准随机种子需在 0 到 {maximum} 之间",
            )

    @staticmethod
    def _shared_seed(
        requested: int | None, model_count: int, required_seeds: int
    ) -> int | None:
        if requested is not None or model_count == 1:
            return requested
        maximum = 2_147_483_647 - max(0, required_seeds - 1)
        return secrets.randbelow(maximum + 1)

    def _job_name(self, base: str, model_id: str, model_count: int) -> str:
        if model_count == 1:
            return base[:80]
        label = str(self._services.profiles.model(model_id).get("label") or model_id)
        return f"{base} · {label}"[:80]
