from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from conftest import FakeEngine, login_as_admin


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
                "username": "studio.member",
                "display_name": "协作成员",
                "password": "1",
            },
        )
        assert created.status_code == 201
        user = created.json()["user"]
        assert _login(member, "studio.member", "1").status_code == 200
        assert member.get("/api/admin/overview").status_code == 403

        disabled = admin.patch(
            f"/api/admin/users/{user['id']}/status", json={"status": "disabled"}
        )
        assert disabled.status_code == 200
        assert member.get("/api/auth/session").status_code == 401
        assert _login(member, "studio.member", "1").status_code == 401

        enabled = admin.patch(
            f"/api/admin/users/{user['id']}/status", json={"status": "active"}
        )
        assert enabled.status_code == 200
        assert _login(member, "studio.member", "1").status_code == 200
        changed = member.post(
            "/api/auth/change-password",
            json={
                "current_password": "1",
                "new_password": "2",
            },
        )
        assert changed.status_code == 200
        assert changed.json()["user"]["must_change_password"] is False


def test_project_invitation_sharing_isolation_and_admin_visibility(
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
        assert alice.get(f"/api/projects/{beta['id']}").status_code == 404
        assert bob.get(f"/api/projects/{alpha['id']}").status_code == 404

        invitation = alice.post(
            f"/api/projects/{alpha['id']}/invitations", json={"username": "bob"}
        )
        assert invitation.status_code == 201
        pending = bob.get("/api/projects").json()["invitations"]
        assert [item["project_id"] for item in pending] == [alpha["id"]]
        accepted = bob.post(f"/api/invitations/{pending[0]['id']}/accept")
        assert accepted.status_code == 200

        members = bob.get(f"/api/projects/{alpha['id']}/members").json()["members"]
        assert {item["username"] for item in members} == {"alice", "bob"}
        assert (
            bob.post(
                f"/api/projects/{alpha['id']}/invitations",
                json={"username": "admin"},
            ).status_code
            == 403
        )

        uploaded = alice.post(
            "/api/scripts",
            data={"project_id": alpha["id"]},
            files={"file": ("shared.txt", "共享台词 | mo-la\n", "text/plain")},
        )
        assert uploaded.status_code == 201
        shared = bob.get(f"/api/scripts?project_id={alpha['id']}")
        assert [item["id"] for item in shared.json()["scripts"]] == [
            uploaded.json()["script"]["id"]
        ]

        all_projects = admin.get("/api/admin/projects").json()["projects"]
        assert {item["id"] for item in all_projects} >= {alpha["id"], beta["id"]}
        actions = {
            item["action"] for item in admin.get("/api/admin/audit-logs").json()["logs"]
        }
        assert {
            "admin.user_created",
            "project.created",
            "project.member_invited",
            "project.invitation_accepted",
            "script.uploaded",
        } <= actions

        bob_user = services.database.auth.get_by_username("bob")
        removed = alice.delete(f"/api/projects/{alpha['id']}/members/{bob_user['id']}")
        assert removed.status_code == 200
        assert bob.get(f"/api/projects/{alpha['id']}").status_code == 404
        assert admin.get(f"/api/projects/{alpha['id']}").status_code == 200


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
