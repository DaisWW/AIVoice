from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from conftest import FakeEngine, login_as_admin, make_wav_bytes


def test_authentication_session_and_bootstrap_password_lifecycle(
    settings_factory,
) -> None:
    settings = settings_factory()
    application = create_app(settings, engine_factory=FakeEngine, seed_legacy=False)

    with TestClient(application) as client:
        services = application.state.services
        bootstrap_password = services.auth.bootstrap_password
        assert bootstrap_password
        assert client.get("/api/healthz").json() == {"ok": True}
        assert client.get("/api/auth/session").status_code == 401
        assert client.get("/api/config").status_code == 401
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "incorrect-password"},
            ).status_code
            == 401
        )

        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": bootstrap_password},
        )
        cookie = response.headers["set-cookie"].lower()
        assert response.status_code == 200
        assert "httponly" in cookie and "samesite=lax" in cookie
        assert client.get("/api/auth/session").json()["user"]["role"] == "system_admin"

        empty_password = client.post(
            "/api/auth/change-password",
            json={"current_password": bootstrap_password, "new_password": ""},
        )
        assert empty_password.status_code == 422

        changed = client.post(
            "/api/auth/change-password",
            json={
                "current_password": bootstrap_password,
                "new_password": "123456",
            },
        )
        assert changed.status_code == 200
        assert changed.json()["user"]["must_change_password"] is False
        assert not (settings.data_root / "bootstrap-admin.txt").exists()
        assert client.post("/api/auth/logout", json={}).status_code == 200
        assert client.get("/api/auth/session").status_code == 401
        assert _login(client, "admin", bootstrap_password).status_code == 401
        assert _login(client, "admin", "123456").status_code == 200

        actions = {item["action"] for item in services.database.audit.list()}
        assert {
            "auth.login_failed",
            "auth.login",
            "auth.password_changed",
            "auth.logout",
        } <= actions


def test_admin_account_status_revokes_sessions(settings_factory) -> None:
    application = create_app(
        settings_factory(), engine_factory=FakeEngine, seed_legacy=False
    )

    with TestClient(application) as admin, TestClient(application) as member:
        login_as_admin(admin, application.state.services)
        created = admin.post(
            "/api/admin/users",
            json={
                "username": "studio.member@example.com",
                "display_name": "协作成员",
                "password": "1",
            },
        )
        assert created.status_code == 201
        user = created.json()["user"]
        assert user["username"] == "studio.member@example.com"
        assert _login(member, "studio.member@example.com", "1").status_code == 200
        assert member.get("/api/admin/overview").status_code == 403

        disabled = admin.patch(
            f"/api/admin/users/{user['id']}/status", json={"status": "disabled"}
        )
        assert disabled.status_code == 200
        assert member.get("/api/auth/session").status_code == 401
        assert _login(member, "studio.member@example.com", "1").status_code == 401

        enabled = admin.patch(
            f"/api/admin/users/{user['id']}/status", json={"status": "active"}
        )
        assert enabled.status_code == 200
        assert _login(member, "studio.member@example.com", "1").status_code == 200
        changed = member.post(
            "/api/auth/change-password",
            json={
                "current_password": "1",
                "new_password": "2",
            },
        )
        assert changed.status_code == 200
        assert changed.json()["user"]["must_change_password"] is False


def test_project_member_sharing_isolation_and_admin_visibility(
    settings_factory,
) -> None:
    application = create_app(
        settings_factory(), engine_factory=FakeEngine, seed_legacy=False
    )

    with (
        TestClient(application) as admin,
        TestClient(application) as alice,
        TestClient(application) as bob,
    ):
        services = application.state.services
        login_as_admin(admin, services)
        _create_user(admin, "alice", "Alice", "alice-password-123")
        _create_user(admin, "bob", "Bob", "bob-password-123")
        assert _login(alice, "alice", "alice-password-123").status_code == 200
        assert _login(bob, "bob", "bob-password-123").status_code == 200

        alpha = _create_project(alice, "Alpha")
        beta = _create_project(bob, "Beta")
        alpha_voice = _create_voice(alice, alpha["id"], "Alpha voice")
        beta_voice = _create_voice(bob, beta["id"], "Beta voice")
        assert alice.get(f"/api/projects/{beta['id']}").status_code == 404
        assert bob.get(f"/api/projects/{alpha['id']}").status_code == 404

        added = alice.post(
            f"/api/projects/{alpha['id']}/members", json={"username": "bob"}
        )
        assert added.status_code == 201
        assert "invitations" not in bob.get("/api/projects").json()
        duplicate = alice.post(
            f"/api/projects/{alpha['id']}/members", json={"username": "bob"}
        )
        assert duplicate.status_code == 409

        members = bob.get(f"/api/projects/{alpha['id']}/members").json()["members"]
        assert {item["username"] for item in members} == {"alice", "bob"}
        alice_user = services.database.auth.get_by_username("alice")
        assert (
            bob.delete(
                f"/api/projects/{alpha['id']}/members/{alice_user['id']}"
            ).status_code
            == 403
        )
        assert (
            bob.post(
                f"/api/projects/{alpha['id']}/members",
                json={"username": "admin"},
            ).status_code
            == 403
        )

        uploaded = alice.post(
            "/api/scripts",
            data={
                "project_id": alpha["id"],
                "default_voice_id": alpha_voice["id"],
            },
            files={"file": ("shared.txt", "共享台词 | mo-la\n", "text/plain")},
        )
        assert uploaded.status_code == 201
        script_id = uploaded.json()["script"]["id"]
        shared = bob.get(f"/api/scripts?project_id={alpha['id']}")
        assert [item["id"] for item in shared.json()["scripts"]] == [script_id]

        cross_project_voice = bob.patch(
            f"/api/scripts/{script_id}",
            json={"name": "shared"},
        )
        assert cross_project_voice.status_code == 200
        assert cross_project_voice.json()["script"]["name"] == "shared"

        script = services.database.scripts.get(script_id)
        assert script
        Path(str(script["source_path"])).unlink()
        cross_project_job = bob.post(
            "/api/jobs",
            data={
                "project_id": beta["id"],
                "model_id": "test_model",
                "script_id": script_id,
                "voice_id": beta_voice["id"],
            },
        )
        assert cross_project_job.status_code == 404

        all_projects = admin.get("/api/admin/projects").json()["projects"]
        assert {item["id"] for item in all_projects} >= {alpha["id"], beta["id"]}
        actions = {
            item["action"] for item in admin.get("/api/admin/audit-logs").json()["logs"]
        }
        assert {
            "admin.user_created",
            "project.created",
            "project.member_added",
            "script.uploaded",
        } <= actions

        bob_user = services.database.auth.get_by_username("bob")
        assert (
            alice.delete(
                f"/api/projects/{alpha['id']}/members/{alice_user['id']}"
            ).status_code
            == 409
        )
        left = bob.delete(f"/api/projects/{alpha['id']}/members/{bob_user['id']}")
        assert left.status_code == 200
        assert bob.get(f"/api/projects/{alpha['id']}").status_code == 404

        readded = alice.post(
            f"/api/projects/{alpha['id']}/members", json={"username": "bob"}
        )
        assert readded.status_code == 201
        removed = alice.delete(f"/api/projects/{alpha['id']}/members/{bob_user['id']}")
        assert removed.status_code == 200
        assert bob.get(f"/api/projects/{alpha['id']}").status_code == 404
        assert admin.get(f"/api/projects/{alpha['id']}").status_code == 404

        actions = {
            item["action"] for item in admin.get("/api/admin/audit-logs").json()["logs"]
        }
        assert {"project.member_left", "project.member_removed"} <= actions


def test_system_admin_workspace_uses_project_membership(
    settings_factory,
) -> None:
    application = create_app(
        settings_factory(), engine_factory=FakeEngine, seed_legacy=False
    )

    with TestClient(application) as admin, TestClient(application) as owner:
        services = application.state.services
        login_as_admin(admin, services)
        _create_user(admin, "owner", "项目负责人", "owner-password-123")
        assert _login(owner, "owner", "owner-password-123").status_code == 200

        project = _create_project(owner, "成员项目")
        workspace_projects = admin.get("/api/projects").json()["projects"]
        assert project["id"] not in {item["id"] for item in workspace_projects}
        assert admin.get(f"/api/projects/{project['id']}").status_code == 404

        admin_projects = admin.get("/api/admin/projects").json()["projects"]
        assert project["id"] in {item["id"] for item in admin_projects}

        added = owner.post(
            f"/api/projects/{project['id']}/members", json={"username": "admin"}
        )
        assert added.status_code == 201

        joined = admin.get(f"/api/projects/{project['id']}").json()["project"]
        assert joined["project_role"] == "member"
        assert joined["can_manage"] is False
        assert (
            admin.post(
                f"/api/projects/{project['id']}/members",
                json={"username": "owner"},
            ).status_code
            == 403
        )

        owned = _create_project(admin, "管理员的成员项目")
        assert owned["project_role"] == "owner"
        assert owned["can_manage"] is True


def test_project_admin_manages_members_but_not_the_owner(settings_factory) -> None:
    application = create_app(
        settings_factory(), engine_factory=FakeEngine, seed_legacy=False
    )

    with (
        TestClient(application) as system_admin,
        TestClient(application) as owner,
        TestClient(application) as project_admin,
        TestClient(application) as member,
    ):
        services = application.state.services
        login_as_admin(system_admin, services)
        owner_user = _create_user(
            system_admin, "project.owner", "项目所有者", "owner-password-123"
        )
        admin_user = _create_user(
            system_admin, "project.admin", "项目管理员", "admin-password-123"
        )
        member_user = _create_user(
            system_admin, "project.member", "项目成员", "member-password-123"
        )
        assert _login(owner, "project.owner", "owner-password-123").status_code == 200
        assert (
            _login(project_admin, "project.admin", "admin-password-123").status_code
            == 200
        )
        assert (
            _login(member, "project.member", "member-password-123").status_code == 200
        )

        project = _create_project(owner, "管理员协作项目")
        project_id = project["id"]
        assert (
            owner.post(
                f"/api/projects/{project_id}/members",
                json={"username": admin_user["username"]},
            ).status_code
            == 201
        )
        promoted = owner.patch(
            f"/api/projects/{project_id}/members/{admin_user['id']}",
            json={"role": "admin"},
        )
        assert promoted.status_code == 200
        assert promoted.json()["member"]["role"] == "admin"

        admin_project = project_admin.get(f"/api/projects/{project_id}").json()[
            "project"
        ]
        assert admin_project["project_role"] == "admin"
        assert admin_project["can_manage"] is True
        assert (
            project_admin.post(
                f"/api/projects/{project_id}/members",
                json={"username": member_user["username"]},
            ).status_code
            == 201
        )
        assert (
            member.patch(
                f"/api/projects/{project_id}/members/{admin_user['id']}",
                json={"role": "member"},
            ).status_code
            == 403
        )
        assert (
            project_admin.patch(
                f"/api/projects/{project_id}/members/{owner_user['id']}",
                json={"role": "member"},
            ).status_code
            == 409
        )
        assert (
            project_admin.delete(
                f"/api/projects/{project_id}/members/{owner_user['id']}"
            ).status_code
            == 409
        )
        assert (
            project_admin.patch(
                f"/api/projects/{project_id}/members/{member_user['id']}",
                json={"role": "reviewer"},
            ).status_code
            == 422
        )


def test_project_role_migration_restores_canonical_owner(settings_factory) -> None:
    settings = settings_factory()
    application = create_app(settings, engine_factory=FakeEngine, seed_legacy=False)

    with TestClient(application):
        services = application.state.services
        owner, _ = services.auth.create_user("owner", "Owner", "owner-password-123")
        ghost, _ = services.auth.create_user("ghost", "Ghost", "ghost-password-123")
        reviewer, _ = services.auth.create_user(
            "reviewer", "Reviewer", "reviewer-password-123"
        )
        project = services.database.projects.create(str(owner["id"]), "迁移项目", "")
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute(
                "DELETE FROM project_members WHERE project_id=? AND user_id=?",
                (project["id"], owner["id"]),
            )
            connection.execute(
                """
                INSERT INTO project_members(project_id, user_id, role, added_by, joined_at)
                VALUES (?, ?, 'member', ?, ?), (?, ?, 'owner', ?, ?),
                       (?, ?, 'reviewer', ?, ?)
                """,
                (
                    project["id"],
                    owner["id"],
                    owner["id"],
                    project["created_at"],
                    project["id"],
                    ghost["id"],
                    owner["id"],
                    project["created_at"],
                    project["id"],
                    reviewer["id"],
                    owner["id"],
                    project["created_at"],
                ),
            )

        services.database.initialize()

        assert services.database.projects.role(project["id"], owner["id"]) == "owner"
        assert services.database.projects.role(project["id"], ghost["id"]) == "member"
        assert (
            services.database.projects.role(project["id"], reviewer["id"]) == "member"
        )


def test_admin_control_room_exposes_insights_assets_and_project_detail(
    settings_factory,
) -> None:
    application = create_app(
        settings_factory(), engine_factory=FakeEngine, seed_legacy=False
    )
    with TestClient(application) as admin:
        services = application.state.services
        login_as_admin(admin, services)
        overview = admin.get("/api/admin/overview")
        assert overview.status_code == 200
        insights = overview.json()["insights"]
        assert {"activity", "failure_reasons", "top_users", "top_projects"} <= set(
            insights
        )
        project = _create_project(admin, "运营数据项目")
        voice = _create_voice(admin, project["id"], "运营声音")
        uploaded = admin.post(
            "/api/scripts",
            data={
                "project_id": project["id"],
                "default_voice_id": voice["id"],
            },
            files={"file": ("lines.txt", "共享台词 | mo-la\n", "text/plain")},
        )
        assert uploaded.status_code == 201

        assets = admin.get("/api/admin/assets")
        assert assets.status_code == 200
        assert assets.json()["scripts"][0]["project_name"] == "运营数据项目"
        detail = admin.get(f"/api/admin/projects/{project['id']}")
        assert detail.status_code == 200
        assert detail.json()["project"]["script_count"] == 1
        assert detail.json()["members"][0]["role"] == "owner"


def _login(client: TestClient, username: str, password: str):
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )


def _create_user(
    admin: TestClient, username: str, display_name: str, password: str
) -> dict:
    response = admin.post(
        "/api/admin/users",
        json={
            "username": username,
            "display_name": display_name,
            "password": password,
        },
    )
    assert response.status_code == 201
    return response.json()["user"]


def _create_project(client: TestClient, name: str) -> dict:
    response = client.post(
        "/api/projects", json={"name": name, "description": f"{name} project"}
    )
    assert response.status_code == 201
    return response.json()["project"]


def _create_voice(client: TestClient, project_id: str, name: str) -> dict:
    response = client.post(
        "/api/voices",
        data={"project_id": project_id, "name": name},
        files=[("files", ("source.wav", make_wav_bytes(), "audio/wav"))],
    )
    assert response.status_code == 201
    return response.json()["voice"]
