from __future__ import annotations

import json
import math
import os
import stat
import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


REASONING_EFFORTS = {"", "none", "minimal", "low", "medium", "high", "max"}
CONFIG_ERROR = "文本模型配置无效，请在管理员设置中修复"
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


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes", "on"}:
        return True
    if isinstance(value, str) and value.strip().lower() in {
        "false",
        "0",
        "no",
        "off",
        "",
    }:
        return False
    raise ValueError(CONFIG_ERROR)


def _as_int(
    value: Any,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if value is None or value == "":
        value = default
    if isinstance(value, bool):
        raise ValueError(CONFIG_ERROR)
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(CONFIG_ERROR) from error
    if parsed < minimum or parsed > maximum:
        raise ValueError(CONFIG_ERROR)
    return parsed


def _as_float(
    value: Any,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if value is None or value == "":
        value = default
    if isinstance(value, bool):
        raise ValueError(CONFIG_ERROR)
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(CONFIG_ERROR) from error
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise ValueError(CONFIG_ERROR)
    return parsed


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
            try:
                payload = {**deepcopy(DEFAULT_TEXT_MODEL), **raw}
                self._validate(payload)
                return TextModelConfig(
                    enabled=_as_bool(payload.get("enabled")),
                    label=str(payload.get("label") or DEFAULT_TEXT_MODEL["label"]),
                    base_url=str(payload.get("base_url") or "").rstrip("/"),
                    api_key=str(payload.get("api_key") or ""),
                    model=str(payload.get("model") or ""),
                    protocol=str(payload.get("protocol") or "responses"),
                    reasoning_effort=str(payload.get("reasoning_effort") or ""),
                    timeout_seconds=_as_int(
                        payload.get("timeout_seconds"),
                        180,
                        minimum=10,
                        maximum=600,
                    ),
                    max_output_tokens=_as_int(
                        payload.get("max_output_tokens"),
                        4000,
                        minimum=128,
                        maximum=32000,
                    ),
                    temperature=_as_float(
                        payload.get("temperature"),
                        0.7,
                        minimum=0,
                        maximum=2,
                    ),
                )
            except (TypeError, ValueError, OverflowError) as error:
                raise RuntimeError(CONFIG_ERROR) from error

    def public(self) -> dict[str, Any]:
        try:
            config = self.config()
        except RuntimeError:
            return {
                "enabled": False,
                "configured": False,
                "label": DEFAULT_TEXT_MODEL["label"],
                "model": "",
                "protocol": "responses",
                "invalid": True,
            }
        return {
            "enabled": config.enabled,
            "configured": config.configured,
            "label": config.label,
            "model": config.model,
            "protocol": config.protocol,
        }

    def editable(self) -> dict[str, Any]:
        try:
            config = self.config()
        except RuntimeError:
            return {
                **self.public(),
                "base_url": "",
                "has_api_key": False,
                "timeout_seconds": DEFAULT_TEXT_MODEL["timeout_seconds"],
                "max_output_tokens": DEFAULT_TEXT_MODEL["max_output_tokens"],
                "reasoning_effort": DEFAULT_TEXT_MODEL["reasoning_effort"],
                "temperature": DEFAULT_TEXT_MODEL["temperature"],
            }
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
            try:
                current = self._read()
            except RuntimeError:
                # A valid admin update must be able to replace a truncated or
                # hand-edited file; an unreadable old key cannot be preserved.
                current = deepcopy(DEFAULT_TEXT_MODEL)
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
                "timeout_seconds": _as_int(
                    changes.get("timeout_seconds", current.get("timeout_seconds")),
                    180,
                    minimum=10,
                    maximum=600,
                ),
                "max_output_tokens": _as_int(
                    changes.get("max_output_tokens", current.get("max_output_tokens")),
                    4000,
                    minimum=128,
                    maximum=32000,
                ),
                "temperature": _as_float(
                    changes.get("temperature", current.get("temperature")),
                    0.7,
                    minimum=0,
                    maximum=2,
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
                raise RuntimeError(CONFIG_ERROR) from error
            if not isinstance(value, dict):
                raise RuntimeError(CONFIG_ERROR)
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
            try:
                parsed = urlsplit(base_url)
                parsed.port
            except ValueError as error:
                raise ValueError(CONFIG_ERROR) from error
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(CONFIG_ERROR)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError(CONFIG_ERROR)
            if parsed.path not in {"", "/", "/v1"}:
                raise ValueError(CONFIG_ERROR)
        model = str(payload.get("model") or "").strip()
        if str(payload.get("protocol")) not in {"responses", "chat_completions"}:
            raise ValueError(CONFIG_ERROR)
        if str(payload.get("reasoning_effort") or "") not in REASONING_EFFORTS:
            raise ValueError(CONFIG_ERROR)
        _as_int(payload.get("timeout_seconds"), 180, minimum=10, maximum=600)
        _as_int(
            payload.get("max_output_tokens"),
            4000,
            minimum=128,
            maximum=32000,
        )
        _as_float(payload.get("temperature"), 0.7, minimum=0, maximum=2)
        if _as_bool(payload.get("enabled")) and not (
            base_url and model and str(payload.get("api_key") or "")
        ):
            raise ValueError(CONFIG_ERROR)


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
        except httpx.InvalidURL as error:
            raise RuntimeError("文本模型 API 地址无效") from error
        except httpx.RequestError as error:
            raise RuntimeError("文本模型网络请求失败") from error
        if response.status_code >= 400:
            raise RuntimeError(f"文本模型请求失败（HTTP {response.status_code}）")
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
                f"请完善{('项目总体' if scope == 'project' else '角色台词特性')}提示词。"
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
            "你是专业台词编剧。根据给定的项目级上下文和角色台词特性创作中文台词。"
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

    def analyze_script_import(
        self,
        *,
        project_prompt: str,
        units: list[dict[str, Any]],
    ) -> dict[str, Any]:
        config = self.store.config()
        system = (
            "你是台本结构分析器。只识别台本归属、角色、台词和非台词，并标注原文字符区间；"
            "绝对不要改写、纠错、补充、翻译或复述任何原文。"
            '只返回 JSON 对象，格式为 {"segments":[{"unit_id":1,"start":0,"end":8,'
            '"content_start":2,"script":"场次或台本名","speaker":"角色名",'
            '"kind":"dialogue"}]}，不要 Markdown 或额外字段。'
            "start、end 使用 Python 字符下标且 end 不包含；每个单元的全部字符必须被连续、无重叠地归类一次，"
            "标题、场景说明、舞台指示等使用 kind=note。"
            "existing_segment=true 表示用户已经分好段：该单元必须只返回一个从 0 到 length 的完整区间，不能再拆句。"
            "只有 existing_segment=false 的连续文本才能按角色变化和自然句意拆分。"
            "content_start 通常等于 start；仅当 dialogue 区间以原文中的‘角色名：’或‘角色名:’开头时，"
            "才可设为冒号后的下标以排除角色标签。未知的台本名或角色名返回空字符串。"
        )
        user = json.dumps(
            {"project_context": project_prompt.strip(), "units": units},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        raw = self.client.complete(config, system=system, user=user)
        value = self._parse_json_object(raw)
        if not isinstance(value, dict) or not isinstance(value.get("segments"), list):
            raise RuntimeError("文本模型没有返回有效的台本分析结果")
        return value

    def rewrite_line(
        self,
        *,
        project_prompt: str,
        script_prompt: str,
        text: str,
        pronunciation: str,
        instruction: str,
    ) -> dict[str, str]:
        config = self.store.config()
        system = (
            "你是专业台词编辑。根据项目级上下文和角色台词特性，只修改用户提供的这一行台词。"
            '只返回 JSON 对象，格式为 {"lines":[{"text":"修改后的台词","pronunciation":"修改后的发音"}]}，'
            "不要 Markdown、解释或额外字段。不要改写成多行；发音没有特殊要求时与台词相同。"
        )
        user = self._layered_context(
            project_prompt=project_prompt,
            script_prompt=script_prompt,
            task=(
                f"当前台词：{text.strip()}\n"
                f"当前发音：{pronunciation.strip() or text.strip()}\n"
                f"修改要求：{instruction.strip()}\n"
                "请给出一个修改候选，不要直接替用户做最终决定。"
            ),
        )
        raw = self.client.complete(config, system=system, user=user)
        return self._parse_lines(raw, 1)[0]

    @staticmethod
    def _layered_context(*, project_prompt: str, script_prompt: str, task: str) -> str:
        return (
            "【项目级上下文】\n"
            f"{project_prompt.strip() or '（未设置）'}\n\n"
            "【角色台词特性】\n"
            f"{script_prompt.strip() or '（未设置）'}\n\n"
            "【本次任务】\n"
            f"{task.strip()}"
        )

    @staticmethod
    def _parse_lines(raw: str, line_count: int) -> list[dict[str, str]]:
        value = TextGenerationService._parse_json_object(raw)
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
            pronunciation = (
                str(item.get("pronunciation") or text).strip()
                if isinstance(item, dict)
                else text
            )
            result.append({"text": text, "pronunciation": pronunciation or text})
        if not result:
            raise RuntimeError("文本模型没有生成有效台词")
        return result

    @staticmethod
    def _parse_json_object(raw: str) -> dict[str, Any] | None:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            value = None
            for start, character in enumerate(raw):
                if character != "{":
                    continue
                try:
                    candidate, _ = decoder.raw_decode(raw[start:])
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    value = candidate
                    break
        return value if isinstance(value, dict) else None
