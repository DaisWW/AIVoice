from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.text_generation import (
    TextGenerationClient,
    TextGenerationService,
    TextModelConfigStore,
)


def test_text_model_endpoint_and_response_content() -> None:
    assert TextGenerationClient._endpoint("https://llm.example/v1", "responses") == (
        "https://llm.example/v1/responses"
    )
    assert TextGenerationClient._endpoint(
        "https://llm.example", "chat_completions"
    ) == ("https://llm.example/v1/chat/completions")
    assert (
        TextGenerationClient._content(
            {"choices": [{"message": {"content": "你好"}}]}, "chat_completions"
        )
        == "你好"
    )
    assert TextGenerationClient._content({"output_text": "台词"}, "responses") == "台词"


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ('说明文字 {不是 JSON} 后续 {"lines":[{"text":"你好"}]}', {"lines": [{"text": "你好"}]}),
        ('{"first": 1} {"second": 2}', {"first": 1}),
        ('{"text":"包含 {大括号}"}', {"text": "包含 {大括号}"}),
    ),
)
def test_parse_json_object_extracts_first_valid_object(raw, expected) -> None:
    assert TextGenerationService._parse_json_object(raw) == expected


@pytest.mark.parametrize(
    "payload",
    (
        {"timeout_seconds": {}},
        {"max_output_tokens": "not-a-number"},
        {"temperature": "nan"},
        {"temperature": "inf"},
    ),
)
def test_corrupt_text_model_config_is_reported_without_raising_public_status(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    path = tmp_path / "text-model.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    store = TextModelConfigStore(path)

    public = store.public()

    assert public["invalid"] is True
    with pytest.raises(RuntimeError, match="配置无效"):
        store.config()


def test_text_model_config_rejects_invalid_port(tmp_path: Path) -> None:
    store = TextModelConfigStore(tmp_path / "text-model.json")

    with pytest.raises(ValueError, match="配置无效"):
        store.update(
            {
                "enabled": False,
                "label": "模型",
                "base_url": "https://llm.example:invalid",
                "model": "test",
                "protocol": "responses",
                "reasoning_effort": "",
                "timeout_seconds": 30,
                "max_output_tokens": 1000,
                "temperature": 0.7,
            }
        )


def test_admin_text_model_get_handles_corrupt_config_without_path_leak(
    app_client,
) -> None:
    client, services = app_client
    services.text_generation.store.path.write_text("{", encoding="utf-8")

    response = client.get("/api/admin/text-model")

    assert response.status_code == 200
    payload = response.json()["text_model"]
    assert payload["invalid"] is True
    assert str(services.text_generation.store.path) not in response.text


def test_admin_text_model_patch_replaces_corrupt_config(app_client) -> None:
    client, services = app_client
    services.text_generation.store.path.write_text("{", encoding="utf-8")

    response = client.patch(
        "/api/admin/text-model",
        json={
            "enabled": False,
            "label": "修复后的模型",
            "base_url": "https://llm.example/v1",
            "api_key": "new-secret",
            "model": "provider/test-model",
            "protocol": "responses",
            "reasoning_effort": "",
            "timeout_seconds": 30,
            "max_output_tokens": 1000,
            "temperature": 0.7,
        },
    )

    assert response.status_code == 200
    assert services.text_generation.store.config().api_key == "new-secret"


def test_text_model_invalid_url_is_safe_error(monkeypatch, tmp_path: Path) -> None:
    store = TextModelConfigStore(tmp_path / "text-model.json")
    store.update(
        {
            "enabled": True,
            "label": "模型",
            "base_url": "https://llm.example/v1",
            "api_key": "secret",
            "model": "test",
            "protocol": "responses",
            "reasoning_effort": "",
            "timeout_seconds": 30,
            "max_output_tokens": 1000,
            "temperature": 0.7,
        }
    )

    def fail(*args, **kwargs):
        del args, kwargs
        raise httpx.InvalidURL("invalid")

    monkeypatch.setattr("app.text_generation.httpx.post", fail)

    with pytest.raises(RuntimeError, match="API 地址无效"):
        TextGenerationClient().complete(store.config(), system="s", user="u")


def test_prompt_suggestion_and_text_generation_layer_prompts(app_client) -> None:
    client, services = app_client
    project = services.database.projects.list_for_user(
        str(services.database.auth.get_by_username("admin")["id"])
    )[0]
    script_response = client.post(
        "/api/scripts",
        files={"file": ("draft.txt", "第一句 | mo-la\n", "text/plain")},
        data={"project_id": project["id"]},
    )
    assert script_response.status_code == 201
    script_id = script_response.json()["script"]["id"]
    client.patch(
        f"/api/projects/{project['id']}",
        json={"name": project["name"], "description": "", "prompt": "总体设定"},
    )
    client.patch(
        f"/api/scripts/{script_id}",
        json={"name": "draft", "prompt": "单台本设定"},
    )

    class FakeTextClient:
        def __init__(self) -> None:
            self.users: list[str] = []

        def complete(self, config, *, system: str, user: str) -> str:
            del config, system
            self.users.append(user)
            if "修改要求：" in user:
                return json.dumps(
                    {"lines": [{"text": "更克制的新台词", "pronunciation": "更克制的新台词"}]}
                )
            if "生成" in user:
                return json.dumps({"lines": [{"text": "新台词一"}, {"text": "新台词二"}]})
            return "完善后的提示词"

    fake = FakeTextClient()
    services.text_generation.client = fake
    services.text_generation.store.update(
        {
            "enabled": True,
            "label": "测试文本模型",
            "base_url": "https://llm.example/v1",
            "api_key": "secret",
            "model": "provider/test-model",
            "protocol": "responses",
            "reasoning_effort": "",
            "timeout_seconds": 30,
            "max_output_tokens": 1000,
            "temperature": 0.7,
        }
    )

    suggestion = client.post(
        f"/api/scripts/{script_id}/prompt-suggestion", json={"goal": "补充表达限制"}
    )
    assert suggestion.status_code == 200
    assert suggestion.json()["suggestion"] == "完善后的提示词"

    generated = client.post(
        f"/api/scripts/{script_id}/generate-text",
        json={"instruction": "生成两句", "line_count": 2},
    )
    assert generated.status_code == 200
    assert generated.json()["lines"] == [
        {"text": "新台词一", "pronunciation": "新台词一"},
        {"text": "新台词二", "pronunciation": "新台词二"},
    ]
    assert "总体设定" in fake.users[-1]
    assert "单台本设定" in fake.users[-1]

    rewritten = client.post(
        f"/api/scripts/{script_id}/rewrite-line",
        json={
            "sequence": 1,
            "text": "用户刚刚手动修改、尚未保存的台词",
            "pronunciation": "用户刚刚手动修改、尚未保存的台词",
            "instruction": "语气更克制",
        },
    )
    assert rewritten.status_code == 200
    assert rewritten.json()["line"] == {
        "text": "更克制的新台词",
        "pronunciation": "更克制的新台词",
    }
    assert "总体设定" in fake.users[-1]
    assert "单台本设定" in fake.users[-1]
    assert "用户刚刚手动修改、尚未保存的台词" in fake.users[-1]
    assert "语气更克制" in fake.users[-1]

    rewritten_script = rewritten.json()["script"]
    rewritten_item = rewritten_script["items"][0]
    assert rewritten_item["text"] == "用户刚刚手动修改、尚未保存的台词"
    assert rewritten_item["pronunciation"] == "用户刚刚手动修改、尚未保存的台词"
    assert rewritten_item["rewrite_instruction"] == "语气更克制"
    repeat = client.post(
        f"/api/scripts/{script_id}/rewrite-line",
        json={
            "sequence": 1,
            "text": "用户刚刚手动修改、尚未保存的台词",
            "pronunciation": "用户刚刚手动修改、尚未保存的台词",
            "instruction": "语气更克制",
            "version": rewritten_script["version"],
        },
    )
    assert repeat.status_code == 200
    assert repeat.json()["script"]["version"] == rewritten_script["version"]
    stale = client.post(
        f"/api/scripts/{script_id}/rewrite-line",
        json={
            "sequence": 1,
            "text": "用户刚刚手动修改、尚未保存的台词",
            "pronunciation": "用户刚刚手动修改、尚未保存的台词",
            "instruction": "语气更克制",
            "version": rewritten_script["version"] - 1,
        },
    )
    assert stale.status_code == 409

    unchanged = client.get(f"/api/scripts/{script_id}")
    assert unchanged.status_code == 200
    assert unchanged.json()["script"]["items"][0]["text"] == "用户刚刚手动修改、尚未保存的台词"
    assert unchanged.json()["script"]["items"][0]["rewrite_instruction"] == "语气更克制"


def test_generate_pronunciations_uses_saved_line_instructions(app_client) -> None:
    client, services = app_client
    project = services.database.projects.list_for_user(
        str(services.database.auth.get_by_username("admin")["id"])
    )[0]
    created = client.post(
        "/api/scripts",
        files={
            "file": ("pronunciation.txt", "第一句 | mo-la\n第二句 | gu-la\n", "text/plain")
        },
        data={"project_id": project["id"]},
    )
    assert created.status_code == 201
    script_id = created.json()["script"]["id"]
    client.patch(
        f"/api/projects/{project['id']}",
        json={"name": project["name"], "description": "", "prompt": "项目总体设定"},
    )
    script = client.patch(
        f"/api/scripts/{script_id}",
        json={"name": "pronunciation", "prompt": "台本角色特性"},
    ).json()["script"]
    saved = client.put(
        f"/api/scripts/{script_id}/items",
        json={
            "version": script["version"],
            "items": [
                {
                    "text": "第一句",
                    "pronunciation": "mo-la",
                    "rewrite_instruction": "第一句要更轻声",
                },
                {
                    "text": "第二句",
                    "pronunciation": "gu-la",
                    "rewrite_instruction": "第二句要停顿更长",
                },
            ],
        },
    )
    assert saved.status_code == 200

    class FakeTextClient:
        def __init__(self) -> None:
            self.user = ""

        def complete(self, config, *, system: str, user: str) -> str:
            del config, system
            self.user = user
            return json.dumps(
                {
                    "lines": [
                        {"sequence": 2, "pronunciation": "第二句的批量发音"},
                        {"sequence": 1, "pronunciation": "第一句的批量发音"},
                    ]
                }
            )

    fake = FakeTextClient()
    services.text_generation.client = fake
    services.text_generation.store.update(
        {
            "enabled": True,
            "label": "测试文本模型",
            "base_url": "https://llm.example/v1",
            "api_key": "secret",
            "model": "provider/test-model",
            "protocol": "responses",
            "reasoning_effort": "",
            "timeout_seconds": 30,
            "max_output_tokens": 1000,
            "temperature": 0.7,
        }
    )

    response = client.post(
        f"/api/scripts/{script_id}/generate-pronunciations",
    )

    assert response.status_code == 200
    assert response.json()["lines"] == [
        {"sequence": 1, "pronunciation": "第一句的批量发音"},
        {"sequence": 2, "pronunciation": "第二句的批量发音"},
    ]
    assert "项目总体设定" in fake.user
    assert "台本角色特性" in fake.user
    assert '"rewrite_instruction":"第一句要更轻声"' in fake.user
    assert '"rewrite_instruction":"第二句要停顿更长"' in fake.user


def test_admin_text_model_config_masks_key(app_client) -> None:
    client, services = app_client
    response = client.patch(
        "/api/admin/text-model",
        json={
            "enabled": False,
            "label": "内部模型",
            "base_url": "https://llm.example/v1",
            "api_key": "secret-key",
            "model": "provider/test-model",
            "protocol": "responses",
            "reasoning_effort": "",
            "timeout_seconds": 30,
            "max_output_tokens": 1000,
            "temperature": 0.7,
        },
    )
    assert response.status_code == 200
    payload = response.json()["text_model"]
    assert "api_key" not in payload
    assert payload["has_api_key"] is True
    assert services.text_generation.store.config().api_key == "secret-key"


def test_smart_script_import_requires_confirmation_and_keeps_exact_dialogue(
    app_client,
) -> None:
    client, services = app_client
    project = services.database.projects.list_for_user(
        str(services.database.auth.get_by_username("admin")["id"])
    )[0]

    class FakeImportClient:
        @staticmethod
        def complete(config, *, system: str, user: str) -> str:
            del config
            assert "绝对不要改写" in system
            payload = json.loads(user)
            assert payload["units"][0]["content"] == "第一幕甲：你好。乙：我来了。"
            assert payload["units"][0]["existing_segment"] is False
            return json.dumps(
                {
                    "segments": [
                        {
                            "unit_id": 1,
                            "start": 0,
                            "end": 3,
                            "content_start": 0,
                            "script": "第一幕",
                            "speaker": "",
                            "kind": "note",
                        },
                        {
                            "unit_id": 1,
                            "start": 3,
                            "end": 8,
                            "content_start": 5,
                            "script": "第一幕",
                            "speaker": "甲",
                            "kind": "dialogue",
                        },
                        {
                            "unit_id": 1,
                            "start": 8,
                            "end": 14,
                            "content_start": 10,
                            "script": "第一幕",
                            "speaker": "乙",
                            "kind": "dialogue",
                        },
                    ]
                },
                ensure_ascii=False,
            )

    services.text_generation.client = FakeImportClient()
    services.text_generation.store.update(
        {
            "enabled": True,
            "label": "测试文本模型",
            "base_url": "https://llm.example/v1",
            "api_key": "secret",
            "model": "provider/test-model",
            "protocol": "responses",
            "reasoning_effort": "",
            "timeout_seconds": 30,
            "max_output_tokens": 1000,
            "temperature": 0.7,
        }
    )

    analyzed = client.post(
        f"/api/projects/{project['id']}/script-imports/analyze",
        files={
            "files": (
                "raw.txt",
                "第一幕甲：你好。乙：我来了。".encode(),
                "text/plain",
            )
        },
    )
    assert analyzed.status_code == 200
    batch = analyzed.json()["batch"]
    assert [draft["speaker"] for draft in batch["drafts"]] == ["甲", "乙"]
    assert [draft["lines"][0]["text"] for draft in batch["drafts"]] == [
        "你好。",
        "我来了。",
    ]
    assert services.database.scripts.list(str(project["id"])) == []

    pending = client.get(f"/api/projects/{project['id']}/script-imports/pending")
    assert pending.status_code == 200
    assert pending.json()["batch"]["batch_id"] == batch["batch_id"]

    confirmed = client.post(
        f"/api/projects/{project['id']}/script-imports/confirm",
        json={
            "batch_id": batch["batch_id"],
            "drafts": [
                {"id": draft["id"], "name": draft["name"]} for draft in batch["drafts"]
            ],
        },
    )
    assert confirmed.status_code == 201
    scripts = confirmed.json()["scripts"]
    assert len(scripts) == 2
    assert [
        client.get(f"/api/scripts/{script['id']}").json()["script"]["items"][0]["text"]
        for script in scripts
    ] == ["你好。", "我来了。"]

    repeated = client.post(
        f"/api/projects/{project['id']}/script-imports/confirm",
        json={
            "batch_id": batch["batch_id"],
            "drafts": [
                {"id": draft["id"], "name": draft["name"]} for draft in batch["drafts"]
            ],
        },
    )
    assert repeated.status_code == 422
    assert len(services.database.scripts.list(str(project["id"]))) == 2


def test_corrupt_smart_import_manifest_requires_explicit_discard(app_client) -> None:
    client, services = app_client
    user = services.database.auth.get_by_username("admin")
    assert user is not None
    project = services.database.projects.list_for_user(str(user["id"]))[0]
    manifest_path = (
        services.settings.data_root
        / "script-imports"
        / str(project["id"])
        / str(user["id"])
        / "pending.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "project_id": project["id"],
                "user_id": user["id"],
                "batch": {"batch_id": "import-corrupt"},
            }
        ),
        encoding="utf-8",
    )

    pending = client.get(f"/api/projects/{project['id']}/script-imports/pending")

    assert pending.status_code == 409
    assert "待确认台本数据" in pending.json()["detail"]
    assert manifest_path.exists()

    discarded = client.delete(f"/api/projects/{project['id']}/script-imports/pending")

    assert discarded.status_code == 204
    assert not manifest_path.exists()
