from __future__ import annotations

from fastapi.testclient import TestClient

from app.database import Database
from app.main import create_app
from app.search import matches_search, search_text

from conftest import FakeEngine, login_as_admin


def test_search_matches_text_pinyin_initials_and_whitespace() -> None:
    assert matches_search("项目", ("项目管理员",))
    assert matches_search("xiangmu", ("项目管理员",))
    assert matches_search("xmgly", ("项目管理员",))
    assert matches_search("  ALI CE ", ("Alice",))
    assert not matches_search("", ("项目管理员",))
    assert "xiangmuguanliyuan" in search_text("项目管理员")


def test_member_candidates_exclude_members_and_inactive_users(settings_factory) -> None:
    settings = settings_factory()
    database = Database(settings.database_path)
    database.initialize()
    owner = database.auth.create_user("owner", "项目负责人", "hash")
    member = database.auth.create_user("member", "已加入成员", "hash")
    candidate = database.auth.create_user("candidate", "项目管理员候选", "hash")
    inactive = database.auth.create_user("inactive", "停用候选", "hash")
    database.auth.update_status(str(inactive["id"]), "disabled")
    project = database.projects.create(str(owner["id"]), "测试项目", "")
    database.projects.add_member(project["id"], str(member["id"]), str(owner["id"]))

    assert database.auth.member_candidates(project["id"], "xmgly") == [
        {
            "id": candidate["id"],
            "username": candidate["username"],
            "display_name": candidate["display_name"],
        }
    ]
    assert database.auth.member_candidates(project["id"], "candidate", limit=0) == []


def test_member_candidates_endpoint_requires_project_manager(settings_factory) -> None:
    application = create_app(
        settings_factory(), engine_factory=FakeEngine, seed_legacy=False
    )
    with TestClient(application) as admin, TestClient(application) as owner, TestClient(
        application
    ) as member:
        services = application.state.services
        login_as_admin(admin, services)
        owner_user = services.auth.create_user("owner", "项目负责人", "owner-password-123")[
            0
        ]
        member_user = services.auth.create_user(
            "member", "候选成员", "member-password-123"
        )[0]
        candidate = services.auth.create_user(
            "candidate", "可添加成员", "candidate-password-123"
        )[0]
        project = services.database.projects.create(str(owner_user["id"]), "项目", "")
        services.database.projects.add_member(
            project["id"], str(member_user["id"]), str(owner_user["id"])
        )

        assert (
            owner.post(
                "/api/auth/login",
                json={"username": "owner", "password": "owner-password-123"},
            ).status_code
            == 200
        )
        assert (
            member.post(
                "/api/auth/login",
                json={"username": "member", "password": "member-password-123"},
            ).status_code
            == 200
        )
        path = f"/api/projects/{project['id']}/member-candidates?q=ke"
        assert owner.get(path).json()["candidates"] == [
            {
                "id": candidate["id"],
                "username": candidate["username"],
                "display_name": candidate["display_name"],
            }
        ]
        assert member.get(path).status_code == 403
        assert admin.get(path).status_code == 404
