from __future__ import annotations

import json

from app.text_generation import TextGenerationClient


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
