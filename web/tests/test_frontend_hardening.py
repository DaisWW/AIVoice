from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "static"


def test_admin_auth_accent_does_not_reference_cyclic_custom_property():
    css = (STATIC / "css" / "ui-refresh.css").read_text(encoding="utf-8")
    block = css.split("body.admin-page .auth-view", 1)[1].split("}", 1)[0]
    assert "--auth-accent: #8fa3ff" in block
    assert "--auth-accent: var(--accent)" not in block


def test_frontend_breakpoint_and_admin_controls_have_accessible_names():
    refresh = (STATIC / "css" / "ui-refresh.css").read_text(encoding="utf-8")
    admin = (STATIC / "admin.html").read_text(encoding="utf-8")
    assert "@media (max-width: 1240px) and (min-width: 1121px)" in refresh
    assert "body.workstation-page .workflow-link span:last-child" in refresh
    assert "body.admin-page .admin-nav button span" in refresh
    assert admin.count('aria-label="关闭创建账户"') == 1
    assert admin.count('aria-label="关闭重置临时密码"') == 1
    for label in ("系统概览", "账户管理", "项目总览", "生成任务", "资产数据", "审计日志", "模型服务"):
        assert f'aria-label="{label}"' in admin


def test_dynamic_media_uses_same_origin_url_allowlist():
    url_module = (STATIC / "js" / "core" / "url.js").read_text(encoding="utf-8")
    app = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    admin = (STATIC / "js" / "admin.js").read_text(encoding="utf-8")
    assert "url.origin !== window.location.origin" in url_module
    assert 'ALLOWED_PROTOCOLS = new Set(["http:", "https:"])' in url_module
    assert "safeResourceUrl(candidate.audio_url)" in app
    assert "safeResourceUrl(file.audio_url)" in app
    assert "safeResourceUrl(job.download_url)" in admin
    assert "safeResourceUrl(item.audio_url)" in admin
