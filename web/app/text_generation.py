from __future__ import annotations

import json
import os
import re
import stat
import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


REASONING_EFFORTS = {"", "none", "minimal", "low", "medium", "high", "max"}
DEFAULT_TEXT_MODEL = {
    "version": 1,
    "enabled": False,
    "label": "文本台词模型",
    "base_url": "",
    "api_key": "",
    "model": "",
    "protocol": "responses",
    "reasoning_effort": "",
    "timeout_seconds": 180,
    "max_output_tokens": 4000,
    "temperature": 0.7,
}


@dataclass(frozen=True)
class TextModelConfig:
    enabled: bool
    label: str
    base_url: str
    api_key: str
    model: str
    protocol: str
    reasoning_effort: str
    timeout_seconds: int
    max_output_tokens: int
    temperature: float

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.base_url and self.api_key and self.model)


class TextModelConfigStore:
    """管理员维护的文本模型配置；密钥仅保存在服务器数据目录。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def config(self) -> TextModelConfig:
        with self._lock:
            raw = self._read()
            return TextModelConfig(
                enabled=bool(raw.get("enabled")),
                label=str(raw.get("label") or DEFAULT_TEXT_MODEL["label"]),
                base_url=str(raw.get("base_url") or "").rstrip("/"),
                api_key=str(raw.get("api_key") or ""),
                model=str(raw.get("model") or ""),
                protocol=str(raw.get("protocol") or "responses"),
                reasoning_effort=str(raw.get("reasoning_effort") or ""),
                timeout_seconds=int(raw.get("timeout_seconds") or 180),
                max_output_tokens=int(raw.get("max_output_tokens") or 4000),
                temperature=float(
                    raw.get("temperature")
                    if raw.get("temperature") is not None
                    else 0.7
                ),
            )

    def public(self) -> dict[str, Any]:
        config = self.config()
        return {
            "enabled": config.enabled,
            "configured": config.configured,
            "label": config.label,
            "model": config.model,
            "protocol": config.protocol,
        }

    def editable(self) -> dict[str, Any]:
        config = self.config()
        return {
            **self.public(),
            "base_url": config.base_url,
            "has_api_key": bool(config.api_key),
            "timeout_seconds": config.timeout_seconds,
            "max_output_tokens": config.max_output_tokens,
            "reasoning_effort": config.reasoning_effort,
            "temperature": config.temperature,
        }

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self._read()
            supplied_key = str(changes.get("api_key") or "").strip()
            if changes.get("clear_api_key"):
                effective_key = ""
            else:
                effective_key = supplied_key or str(current.get("api_key") or "")
            payload = {
                **current,
                "enabled": bool(changes.get("enabled", current.get("enabled"))),
                "label": str(changes.get("label", current.get("label")) or "").strip(),
                "base_url": str(changes.get("base_url", current.get("base_url")) or "")
                .strip()
                .rstrip("/"),
                "api_key": effective_key,
                "model": str(changes.get("model", current.get("model")) or "").strip(),
                "protocol": str(
                    changes.get("protocol", current.get("protocol")) or "responses"
                ).strip(),
                "reasoning_effort": str(
                    changes.get("reasoning_effort", current.get("reasoning_effort"))
                    or ""
                ).strip(),
                "timeout_seconds": int(
                    changes.get("timeout_seconds", current.get("timeout_seconds"))
                    or 180
                ),
                "max_output_tokens": int(
                    changes.get("max_output_tokens", current.get("max_output_tokens"))
                    or 4000
                ),
                "temperature": float(
                    changes.get("temperature", current.get("temperature"))
                    if changes.get("temperature", current.get("temperature"))
                    is not None
                    else 0.7
                ),
                "version": 1,
            }
            self._validate(payload)
            self._write(payload)
        return self.editable()

    def _read(self) -> dict[str, Any]:
        if self._path.is_file():
            try:
                value = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(f"文本模型配置无法读取: {self._path}") from error
            if not isinstance(value, dict):
                raise RuntimeError("文本模型配置必须是 JSON 对象")
        else:
            value = {}
        payload = {**deepcopy(DEFAULT_TEXT_MODEL), **value}
        env_values = {
            "base_url": os.getenv("VOICE_TEXT_MODEL_BASE_URL", "").strip(),
            "api_key": os.getenv("VOICE_TEXT_MODEL_API_KEY", "").strip(),
            "model": os.getenv("VOICE_TEXT_MODEL", "").strip(),
        }
        for key, value in env_values.items():
            if value:
                payload[key] = value
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        try:
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        os.replace(temporary, self._path)

    @staticmethod
    def _validate(payload: dict[str, Any]) -> None:
        label = str(payload.get("label") or "").strip()
        if not label or len(label) > 100:
            raise ValueError("文本模型名称不能为空且不能超过 100 个字符")
        base_url = str(payload.get("base_url") or "").strip()
        if base_url:
            parsed = urlsplit(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("文本模型 API 地址必须是有效的 HTTP(S) 地址")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("文本模型 API 地址不能包含凭据、查询参数或片段")
            if parsed.path not in {"", "/", "/v1"}:
                raise ValueError("文本模型 API 地址只能填写主机或 /v1")
        model = str(payload.get("model") or "").strip()
        if str(payload.get("protocol")) not in {"responses", "chat_completions"}:
            raise ValueError("文本模型协议必须是 responses 或 chat_completions")
        if str(payload.get("reasoning_effort") or "") not in REASONING_EFFORTS:
            raise ValueError("推理强度配置无效")
        if not 10 <= int(payload.get("timeout_seconds") or 0) <= 600:
            raise ValueError("请求超时必须在 10 到 600 秒之间")
        if not 128 <= int(payload.get("max_output_tokens") or 0) <= 32000:
            raise ValueError("最大输出 token 必须在 128 到 32000 之间")
        temperature = float(payload.get("temperature") or 0)
        if not 0 <= temperature <= 2:
            raise ValueError("温度必须在 0 到 2 之间")
        if bool(payload.get("enabled")) and not (
            base_url and model and str(payload.get("api_key") or "")
        ):
            raise ValueError("启用文本模型前必须配置 API 地址、模型 ID 和 API Key")


class TextGenerationClient:
    def complete(self, config: TextModelConfig, *, system: str, user: str) -> str:
        if not config.configured:
            raise ValueError("文本模型尚未配置或未启用")
        endpoint = self._endpoint(config.base_url, config.protocol)
        if config.protocol == "responses":
            payload: dict[str, Any] = {
                "model": config.model,
                "instructions": system,
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": user}],
                    }
                ],
                "stream": False,
                "max_output_tokens": config.max_output_tokens,
            }
            if config.reasoning_effort:
                payload["reasoning"] = {"effort": config.reasoning_effort}
        else:
            payload = {
                "model": config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": config.max_output_tokens,
                "temperature": config.temperature,
            }
        try:
            response = httpx.post(
                endpoint,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {config.api_key}",
                },
                json=payload,
                timeout=config.timeout_seconds,
            )
        except httpx.TimeoutException as error:
            raise RuntimeError("文本模型请求超时") from error
        except httpx.RequestError as error:
            raise RuntimeError(f"文本模型无法连接: {error}") from error
        if response.status_code >= 400:
            detail = response.text[:500].strip()
            raise RuntimeError(f"文本模型返回 HTTP {response.status_code}: {detail}")
        try:
            body = response.json()
        except ValueError as error:
            raise RuntimeError("文本模型返回的不是有效 JSON") from error
        content = self._content(body, config.protocol)
        if not content.strip():
            raise RuntimeError("文本模型返回了空内容")
        return content.strip()

    @staticmethod
    def _endpoint(base_url: str, protocol: str) -> str:
        base = base_url.rstrip("/")
        suffix = "responses" if protocol == "responses" else "chat/completions"
        return f"{base}/{suffix}" if base.endswith("/v1") else f"{base}/v1/{suffix}"

    @staticmethod
    def _content(body: Any, protocol: str) -> str:
        if protocol == "chat_completions":
            choices = body.get("choices") if isinstance(body, dict) else None
            first = choices[0] if isinstance(choices, list) and choices else {}
            message = first.get("message") if isinstance(first, dict) else {}
            value = message.get("content") if isinstance(message, dict) else ""
            if isinstance(value, list):
                return "".join(
                    str(item.get("text") or "")
                    for item in value
                    if isinstance(item, dict)
                )
            return str(value or "")
        if isinstance(body, dict) and isinstance(body.get("output_text"), str):
            return body["output_text"]
        parts: list[str] = []
        output = body.get("output") if isinstance(body, dict) else None
        for item in output if isinstance(output, list) else []:
            if not isinstance(item, dict):
                continue
            for part in (
                item.get("content") if isinstance(item.get("content"), list) else []
            ):
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
        return "".join(parts)


class TextGenerationService:
    def __init__(
        self, store: TextModelConfigStore, client: TextGenerationClient | None = None
    ) -> None:
        self.store = store
        self.client = client or TextGenerationClient()

    def public(self) -> dict[str, Any]:
        return self.store.public()

    def admin(self) -> dict[str, Any]:
        return self.store.editable()

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        return self.store.update(changes)

    def suggest_prompt(
        self, *, scope: str, project_prompt: str, script_prompt: str, goal: str
    ) -> str:
        config = self.store.config()
        system = (
            "你是台词创作提示词编辑。只输出一份可直接粘贴使用的中文提示词，不要解释、标题、Markdown围栏或 API 信息。"
            "提示词应明确角色、语气、场景、长度、禁用项和输出格式；保留用户已给出的事实，不擅自添加世界观。"
        )
        user = self._layered_context(
            project_prompt=project_prompt,
            script_prompt=script_prompt,
            task=(
                f"请完善{('项目总体' if scope == 'project' else '单台本')}提示词。"
                f"用户补充目标：{goal.strip() or '无'}"
            ),
        )
        return self.client.complete(config, system=system, user=user)

    def generate_lines(
        self,
        *,
        project_prompt: str,
        script_prompt: str,
        instruction: str,
        line_count: int,
        model_id: str = "",
    ) -> list[dict[str, str]]:
        config = self.store.config()
        if model_id and model_id != config.model:
            raise ValueError("当前只配置了一个文本模型，请使用默认模型")
        system = (
            "你是专业台词编剧。根据给定的总体规则和台本规则创作中文台词。"
            '只返回 JSON 对象，格式为 {"lines":[{"text":"台词"}]}，不要 Markdown、编号或额外字段。'
            "每句是可直接交给配音的完整台词，避免解释和舞台指示。"
        )
        user = self._layered_context(
            project_prompt=project_prompt,
            script_prompt=script_prompt,
            task=f"生成 {line_count} 句台词。具体要求：{instruction.strip() or '围绕台本设定推进一段自然对话。'}",
        )
        raw = self.client.complete(config, system=system, user=user)
        return self._parse_lines(raw, line_count)

    @staticmethod
    def _layered_context(*, project_prompt: str, script_prompt: str, task: str) -> str:
        return (
            "【项目总体提示词】\n"
            f"{project_prompt.strip() or '（未设置）'}\n\n"
            "【单台本提示词】\n"
            f"{script_prompt.strip() or '（未设置）'}\n\n"
            "【本次任务】\n"
            f"{task.strip()}"
        )

    @staticmethod
    def _parse_lines(raw: str, line_count: int) -> list[dict[str, str]]:
        value: Any = None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if match:
                try:
                    value = json.loads(match.group(0))
                except json.JSONDecodeError:
                    value = None
        raw_lines = value.get("lines") if isinstance(value, dict) else None
        if not isinstance(raw_lines, list):
            raw_lines = [
                line.strip(" -\t") for line in raw.splitlines() if line.strip()
            ]
        result: list[dict[str, str]] = []
        for item in raw_lines[:line_count]:
            text = (
                str(item.get("text") or "").strip()
                if isinstance(item, dict)
                else str(item).strip()
            )
            if not text:
                continue
            result.append({"text": text, "pronunciation": text})
        if not result:
            raise RuntimeError("文本模型没有生成有效台词")
        return result
