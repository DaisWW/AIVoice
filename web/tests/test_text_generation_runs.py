from __future__ import annotations

import json
import time
from typing import Any


def _configure_text_model(services: Any) -> None:
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


def _script(client: Any, project_id: str) -> dict[str, Any]:
    response = client.post(
        "/api/scripts",
        files={"file": ("draft.txt", "第一句 | mo-la\n第二句 | gu-la\n", "text/plain")},
        data={"project_id": project_id},
    )
    assert response.status_code == 201
    return response.json()["script"]


def _wait_for_run(client: Any, run_id: str, timeout: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/text-generation-runs/{run_id}")
        assert response.status_code == 200
        run = response.json()["run"]
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.02)
    raise AssertionError(f"text generation run did not finish: {run_id}")


def test_async_text_run_persists_progress_history_and_parent_output(app_client) -> None:
    client, services = app_client
    user = services.database.auth.get_by_username("admin")
    assert user is not None
    project = services.database.projects.list_for_user(str(user["id"]))[0]
    script = _script(client, str(project["id"]))
    _configure_text_model(services)

    class FakeTextClient:
        def __init__(self) -> None:
            self.users: list[str] = []

        def complete(self, config: Any, *, system: str, user: str) -> str:
            del config, system
            self.users.append(user)
            if "上一轮生成结果" in user:
                return json.dumps({"lines": [{"text": "基于上一轮的新台词"}]})
            return json.dumps({"lines": [{"text": "第一轮台词"}]})

    fake = FakeTextClient()
    services.text_generation.client = fake
    first_response = client.post(
        f"/api/scripts/{script['id']}/text-generation-runs",
        json={"kind": "lines", "instruction": "第一轮要求", "line_count": 1},
    )
    assert first_response.status_code == 202
    first = _wait_for_run(client, first_response.json()["run"]["id"])
    assert first["status"] == "completed"
    assert first["stage"] == "已完成"
    assert first["result"]["lines"] == [{"text": "第一轮台词", "pronunciation": "第一轮台词"}]

    second_response = client.post(
        f"/api/scripts/{script['id']}/text-generation-runs",
        json={
            "kind": "lines",
            "instruction": "继续压缩",
            "line_count": 1,
            "parent_run_id": first["id"],
        },
    )
    assert second_response.status_code == 202
    second = _wait_for_run(client, second_response.json()["run"]["id"])
    assert second["parent_run_id"] == first["id"]
    assert second["input"]["instruction"] == "继续压缩"
    assert "第一轮台词" in fake.users[-1]

    history = client.get(
        f"/api/projects/{project['id']}/text-generation-runs?script_id={script['id']}"
    )
    assert history.status_code == 200
    assert {run["id"] for run in history.json()["runs"]} >= {first["id"], second["id"]}


def test_text_run_rejects_parent_from_other_scope_and_project_member_can_read_history(
    app_client,
) -> None:
    client, services = app_client
    user = services.database.auth.get_by_username("admin")
    assert user is not None
    project = services.database.projects.list_for_user(str(user["id"]))[0]
    script = _script(client, str(project["id"]))
    _configure_text_model(services)

    class FakeTextClient:
        @staticmethod
        def complete(config: Any, *, system: str, user: str) -> str:
            del config, system, user
            return "项目上下文建议"

    services.text_generation.client = FakeTextClient()
    project_response = client.post(
        f"/api/projects/{project['id']}/text-generation-runs",
        json={"kind": "prompt_suggestion", "goal": "补充规则"},
    )
    assert project_response.status_code == 202
    project_run = _wait_for_run(client, project_response.json()["run"]["id"])

    wrong_scope = client.post(
        f"/api/scripts/{script['id']}/text-generation-runs",
        json={
            "kind": "prompt_suggestion",
            "goal": "继续",
            "parent_run_id": project_run["id"],
        },
    )
    assert wrong_scope.status_code == 422

    project_history = client.get(f"/api/projects/{project['id']}/text-generation-runs")
    assert project_history.status_code == 200
    assert project_history.json()["runs"][0]["id"] == project_run["id"]


def test_text_run_rejects_a_sequence_missing_from_the_script(app_client) -> None:
    client, services = app_client
    user = services.database.auth.get_by_username("admin")
    assert user is not None
    project = services.database.projects.list_for_user(str(user["id"]))[0]
    script = _script(client, str(project["id"]))

    response = client.post(
        f"/api/scripts/{script['id']}/text-generation-runs",
        json={
            "kind": "rewrite_line",
            "sequence": 99,
            "text": "不存在的台词行",
            "pronunciation": "不存在的台词行",
            "instruction": "改写",
        },
    )

    assert response.status_code == 404
    assert (
        services.database.text_generation_runs.list_for_project(
            str(project["id"]), script_id=script["id"]
        )
        == []
    )


def test_script_delete_is_blocked_by_active_text_generation(app_client) -> None:
    client, services = app_client
    user = services.database.auth.get_by_username("admin")
    assert user is not None
    project = services.database.projects.list_for_user(str(user["id"]))[0]
    script = _script(client, str(project["id"]))
    runs = services.database.text_generation_runs
    run = runs.create(
        project_id=str(project["id"]),
        script_id=script["id"],
        created_by=str(user["id"]),
        kind="lines",
        input_data={"kind": "lines"},
        context={"script_version": str(script.get("version") or 1)},
    )

    assert runs.has_active_for_script(script["id"])
    blocked = client.delete(f"/api/scripts/{script['id']}")
    assert blocked.status_code == 409
    assert services.database.scripts.get(script["id"]) is not None

    assert runs.mark_running(run["id"])
    blocked_running = client.delete(f"/api/scripts/{script['id']}")
    assert blocked_running.status_code == 409
    assert runs.has_active_for_script(script["id"])
    assert runs.fail(run["id"], error="测试结束")


def test_context_revisions_can_be_listed_and_restored(app_client) -> None:
    client, services = app_client
    user = services.database.auth.get_by_username("admin")
    assert user is not None
    project = services.database.projects.list_for_user(str(user["id"]))[0]
    script = _script(client, str(project["id"]))

    first_project = client.patch(
        f"/api/projects/{project['id']}",
        json={"name": project["name"], "description": "", "prompt": "项目规则一"},
    )
    assert first_project.status_code == 200
    second_project = client.patch(
        f"/api/projects/{project['id']}",
        json={"name": project["name"], "description": "", "prompt": "项目规则二"},
    )
    assert second_project.status_code == 200
    revisions = client.get(f"/api/projects/{project['id']}/context-revisions")
    assert revisions.status_code == 200
    project_revision = next(
        revision
        for revision in revisions.json()["revisions"]
        if revision["content"] == "项目规则一"
    )
    restored = client.post(
        f"/api/context-revisions/{project_revision['id']}/restore", json={}
    )
    assert restored.status_code == 200
    assert restored.json()["project"]["prompt"] == "项目规则一"

    first_script = client.patch(
        f"/api/scripts/{script['id']}",
        json={"name": script["name"], "prompt": "角色规则一"},
    )
    assert first_script.status_code == 200
    second_script = client.patch(
        f"/api/scripts/{script['id']}",
        json={"name": script["name"], "prompt": "角色规则二"},
    )
    assert second_script.status_code == 200
    script_revisions = client.get(f"/api/scripts/{script['id']}/context-revisions")
    assert script_revisions.status_code == 200
    script_revision = next(
        revision
        for revision in script_revisions.json()["revisions"]
        if revision["content"] == "角色规则一"
    )
    restored_script = client.post(
        f"/api/context-revisions/{script_revision['id']}/restore", json={}
    )
    assert restored_script.status_code == 200
    assert restored_script.json()["script"]["prompt"] == "角色规则一"


def test_smart_import_analysis_is_a_resumable_text_run(app_client) -> None:
    client, services = app_client
    user = services.database.auth.get_by_username("admin")
    assert user is not None
    project = services.database.projects.list_for_user(str(user["id"]))[0]
    _configure_text_model(services)

    class FakeImportClient:
        @staticmethod
        def complete(config: Any, *, system: str, user: str) -> str:
            del config, system
            payload = json.loads(user)
            unit = payload["units"][0]
            text = unit["content"]
            return json.dumps(
                {
                    "segments": [
                        {
                            "unit_id": unit["unit_id"],
                            "start": 0,
                            "end": len(text),
                            "content_start": 0,
                            "script": "第一幕",
                            "speaker": "甲",
                            "kind": "dialogue",
                        }
                    ]
                },
                ensure_ascii=False,
            )

    services.text_generation.client = FakeImportClient()
    response = client.post(
        f"/api/projects/{project['id']}/script-imports/analyze-run",
        files={"files": ("raw.txt", "你好。".encode(), "text/plain")},
    )
    assert response.status_code == 202
    run = _wait_for_run(client, response.json()["run"]["id"])
    assert run["kind"] == "script_import"
    assert run["result"]["batch"]["drafts"][0]["lines"][0]["text"] == "你好。"
    pending = client.get(f"/api/projects/{project['id']}/script-imports/pending")
    assert pending.status_code == 200
    assert pending.json()["batch"]["batch_id"] == run["result"]["batch"]["batch_id"]


def test_completed_smart_import_run_can_restore_discarded_preview(app_client) -> None:
    client, services = app_client
    user = services.database.auth.get_by_username("admin")
    assert user is not None
    project = services.database.projects.list_for_user(str(user["id"]))[0]
    batch = {
        "batch_id": "import-history-restore",
        "created_at": "2026-09-09T00:00:00+00:00",
        "source_files": ["raw.txt"],
        "drafts": [
            {
                "id": "draft-1",
                "name": "第一幕 · 甲",
                "script": "第一幕",
                "speaker": "甲",
                "source_files": ["raw.txt"],
                "line_count": 1,
                "lines": [
                    {
                        "text": "你好。",
                        "pronunciation": "你好。",
                        "source_file": "raw.txt",
                    }
                ],
            }
        ],
        "excluded": [],
        "dialogue_line_count": 1,
        "excluded_count": 0,
    }
    run = services.database.text_generation_runs.create(
        project_id=str(project["id"]),
        script_id=None,
        created_by=str(user["id"]),
        kind="script_import",
        input_data={"kind": "script_import"},
        context={"source_files": ["raw.txt"]},
    )
    assert services.database.text_generation_runs.mark_running(run["id"])
    assert services.database.text_generation_runs.complete(
        run["id"], output_text="已生成 1 个台本候选，1 条台词", result={"batch": batch}
    )

    restored = client.post(
        f"/api/text-generation-runs/{run['id']}/restore-script-import", json={}
    )

    assert restored.status_code == 200
    assert restored.json()["batch"]["batch_id"] == batch["batch_id"]
    pending = client.get(f"/api/projects/{project['id']}/script-imports/pending")
    assert pending.status_code == 200
    assert pending.json()["batch"]["drafts"][0]["lines"][0]["text"] == "你好。"


def test_failed_text_run_keeps_partial_stream_output(app_client) -> None:
    client, services = app_client
    user = services.database.auth.get_by_username("admin")
    assert user is not None
    project = services.database.projects.list_for_user(str(user["id"]))[0]
    script = _script(client, str(project["id"]))
    _configure_text_model(services)

    class PartialTextClient:
        def stream(self, config: Any, *, system: str, user: str, on_delta: Any) -> str:
            del config, system, user
            on_delta("已输出的一部分")
            raise RuntimeError("供应商连接中断")

    services.text_generation.client = PartialTextClient()
    response = client.post(
        f"/api/scripts/{script['id']}/text-generation-runs",
        json={"kind": "lines", "instruction": "测试失败保留输出", "line_count": 1},
    )
    assert response.status_code == 202
    run = _wait_for_run(client, response.json()["run"]["id"])
    assert run["status"] == "failed"
    assert run["output_text"] == "已输出的一部分"
