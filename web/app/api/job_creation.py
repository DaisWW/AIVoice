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
        voice_ids_json: str,
        model_id: str,
        model_ids_json: str,
        script_id: str,
        name: str,
        candidate_count: int,
        reference_emotion: str,
        generation_settings_json: str,
        base_seed: int | None,
    ) -> list[dict[str, Any]]:
        model_id = model_id.strip()
        emotion = self._emotion(reference_emotion)
        model_ids = self._model_ids(model_id, model_ids_json)
        voice_ids = self._voice_ids(requested_voice_id, voice_ids_json)
        self._validate_request(model_ids, name, candidate_count)
        primary_settings = self._settings(model_id, generation_settings_json)
        self._validate_seed(base_seed, 1)
        selected_script = self._script(script_id)
        self._validate_voices(voice_ids, emotion)
        items = ScriptStorage(self._services).load_items(selected_script)
        self._validate_seed(base_seed, len(items) * candidate_count)
        effective_seed = self._shared_seed(
            base_seed,
            len(model_ids) * len(voice_ids),
            len(items) * candidate_count,
        )
        jobs = self._create_jobs(
            str(selected_script["id"]),
            voice_ids,
            model_ids,
            model_id.strip(),
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
        voice_ids: list[str],
        model_ids: list[str],
        primary_model_id: str,
        items: list[ScriptItem],
        base_name: str,
        candidate_count: int,
        emotion: str,
        primary_settings: dict[str, float | int],
        base_seed: int | None,
    ) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for model_id in model_ids:
            settings = (
                primary_settings
                if model_id == primary_model_id
                else self._services.profiles.generation_settings(model_id)
            )
            for voice_id in voice_ids:
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
                    self._job_name(
                        base_name,
                        voice_id,
                        model_id,
                        len(voice_ids),
                        len(model_ids),
                    ),
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

    def _validate_voices(self, voice_ids: list[str], emotion: str) -> None:
        for voice_id in voice_ids:
            self._validate_voice(voice_id, emotion)

    def _validate_voice(self, voice_id: str, emotion: str) -> None:
        voice = self._services.database.voices.get(voice_id)
        if not voice or str(voice.get("project_id") or "") != self._project_id:
            raise HTTPException(status_code=404, detail="找不到所选声音库")
        if int(voice.get("enabled_file_count") or 0) < 1:
            raise HTTPException(status_code=422, detail="所选声音库没有启用的录音")
        if emotion == "all":
            return
        files = self._services.database.voices.list_files(voice_id)
        if not any(item.get("emotion_tag") == emotion for item in files):
            raise HTTPException(status_code=422, detail="所选声音库没有启用该语气分组的录音")

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
        result = list(
            dict.fromkeys(item.strip() for item in [primary, *parsed] if item.strip())
        )
        if not primary.strip() or len(result) > 4:
            raise HTTPException(status_code=422, detail="每次请选择 1-4 个模型")
        return result

    @staticmethod
    def _voice_ids(primary: str, value: str) -> list[str]:
        try:
            parsed = json.loads(value) if value.strip() else []
        except json.JSONDecodeError as error:
            raise HTTPException(status_code=422, detail="对比声音格式错误") from error
        if not isinstance(parsed, list) or any(
            not isinstance(item, str) for item in parsed
        ):
            raise HTTPException(status_code=422, detail="对比声音必须是声音库 ID 数组")
        result = list(
            dict.fromkeys(item.strip() for item in [primary, *parsed] if item.strip())
        )
        if not result or len(result) > 4:
            raise HTTPException(status_code=422, detail="每次请选择 1-4 个声音库")
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
        requested: int | None, comparison_count: int, required_seeds: int
    ) -> int | None:
        if requested is not None or comparison_count == 1:
            return requested
        maximum = 2_147_483_647 - max(0, required_seeds - 1)
        return secrets.randbelow(maximum + 1)

    def _job_name(
        self,
        base: str,
        voice_id: str,
        model_id: str,
        voice_count: int,
        model_count: int,
    ) -> str:
        labels = [base]
        if voice_count > 1:
            voice = self._services.database.voices.get(voice_id) or {}
            labels.append(str(voice.get("name") or voice_id))
        if model_count > 1:
            model = self._services.profiles.model(model_id)
            labels.append(str(model.get("label") or model_id))
        return " · ".join(labels)[:80]
