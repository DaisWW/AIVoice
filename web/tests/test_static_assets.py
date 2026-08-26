from __future__ import annotations

import re
from pathlib import Path


WEB_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = WEB_ROOT / "static"
HTML_ASSET_RE = re.compile(r'(?:src|href)="(/(?:static|assets)/[^"?#]+)"')
MODULE_IMPORT_RE = re.compile(r'from\s+["\']([^"\']+)["\']')
MODULE_ENTRY_RE = re.compile(r'<script\b[^>]*\bsrc="(/(?:static|assets)/[^"?#]+\.js)')
HTML_ID_RE = re.compile(r'\bid=["\']([A-Za-z][\w-]*)["\']')
JS_ID_QUERY_RE = re.compile(r'\$\$?\(\s*["\']#([A-Za-z][\w-]*)["\']')
BY_ID_QUERY_RE = re.compile(r'\bbyId\(\s*["\']([A-Za-z][\w-]*)["\']')


def test_html_pages_reference_existing_static_assets() -> None:
    missing: list[str] = []
    references_found = 0
    for page in STATIC_ROOT.glob("*.html"):
        references = HTML_ASSET_RE.findall(page.read_text(encoding="utf-8"))
        references_found += len(references)
        missing.extend(
            f"{page.name} -> {reference}"
            for reference in references
            if not (
                STATIC_ROOT
                / reference.removeprefix("/static/").removeprefix("/assets/")
            ).is_file()
        )

    assert references_found
    assert not missing


def test_javascript_module_imports_resolve() -> None:
    missing: list[str] = []
    for source in (STATIC_ROOT / "js").rglob("*.js"):
        content = source.read_text(encoding="utf-8")
        for reference in MODULE_IMPORT_RE.findall(content):
            if not reference.startswith("."):
                continue
            target = (source.parent / reference.split("?", 1)[0]).resolve()
            if not target.is_file():
                missing.append(f"{source.relative_to(STATIC_ROOT)} -> {reference}")

    assert not missing


def test_javascript_static_id_queries_resolve() -> None:
    sources = [*STATIC_ROOT.glob("*.html"), *_loaded_modules()]
    contents = [source.read_text(encoding="utf-8") for source in sources]
    declared = {match for content in contents for match in HTML_ID_RE.findall(content)}
    referenced = {
        match for content in contents for match in JS_ID_QUERY_RE.findall(content)
    } | {match for content in contents for match in BY_ID_QUERY_RE.findall(content)}

    assert not sorted(referenced - declared)


def _loaded_modules() -> set[Path]:
    pending = [
        STATIC_ROOT / reference.removeprefix("/static/").removeprefix("/assets/")
        for page in STATIC_ROOT.glob("*.html")
        for reference in MODULE_ENTRY_RE.findall(page.read_text(encoding="utf-8"))
    ]
    loaded: set[Path] = set()
    while pending:
        source = pending.pop().resolve()
        if source in loaded:
            continue
        loaded.add(source)
        for reference in MODULE_IMPORT_RE.findall(source.read_text(encoding="utf-8")):
            if reference.startswith("."):
                pending.append((source.parent / reference.split("?", 1)[0]).resolve())
    return loaded


def test_admin_username_pattern_accepts_email_style_username() -> None:
    content = (STATIC_ROOT / "admin.html").read_text(encoding="utf-8")
    input_tag = re.search(r'<input\b[^>]*\bid="newUsername"[^>]*>', content)

    assert input_tag
    pattern = re.search(r'\bpattern="([^"]+)"', input_tag.group())
    assert pattern
    assert re.fullmatch(pattern.group(1), "member@example.com")


def test_generation_form_only_lists_explicitly_available_models() -> None:
    content = (STATIC_ROOT / "js" / "app.js").read_text(encoding="utf-8")

    assert (
        "this.state.config.models.filter((model) => model.available === true)"
        in content
    )


def test_generation_form_uses_searchable_configuration_rows() -> None:
    content = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_ROOT / "js" / "app.js").read_text(encoding="utf-8")

    assert 'id="generationConfigurationList"' in content
    assert 'data-action="add-generation-configuration"' in content
    assert 'data-action="generation-dropdown-search"' in javascript
    assert 'data-action="toggle-generation-dropdown"' in javascript
    assert 'data-action="select-generation-dropdown-option"' in javascript
    assert (
        "<select"
        not in re.search(r"generationDropdown\(.*?\n  }", javascript, re.DOTALL).group()
    )
    assert '<datalist id="generationVoiceOptions"' not in content
    assert 'id="generationVoiceChoices"' not in content
    assert 'id="generationModelChoices"' not in content


def test_script_editor_requires_confirmation_for_ai_rewrites_and_has_fixed_save() -> (
    None
):
    javascript = (STATIC_ROOT / "js" / "app.js").read_text(encoding="utf-8")
    stylesheet = (STATIC_ROOT / "css" / "workstation.css").read_text(encoding="utf-8")
    refreshed_stylesheet = (STATIC_ROOT / "css" / "ui-refresh.css").read_text(
        encoding="utf-8"
    )

    assert 'data-action="rewrite-line"' in javascript
    assert 'data-action="adopt-line-rewrite"' in javascript
    assert 'data-action="discard-line-rewrite"' in javascript
    assert "rewrite-line`" in javascript
    assert 'class="button button-primary script-save-floating"' in javascript
    assert "角色台词特性" in javascript
    assert "项目级上下文" in javascript
    assert "本次要求" not in javascript
    assert ".script-save-floating { position: fixed;" in stylesheet
    assert (
        "body.workstation-page .line-rewrite-controls .button {\n  min-height: 44px;"
        in (refreshed_stylesheet)
    )


def test_system_admin_has_direct_workstation_admin_button() -> None:
    content = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_ROOT / "js" / "app.js").read_text(encoding="utf-8")

    assert 'id="adminLink" class="admin-link" href="/admin"' in content
    assert "管理后台" in content
    assert 'const isSystemAdmin = user.role === "system_admin";' in javascript
    assert 'byId("adminLink").hidden = !isSystemAdmin;' in javascript


def test_workstation_login_uses_generated_background_asset() -> None:
    content = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    stylesheet = (STATIC_ROOT / "css" / "ui-refresh.css").read_text(encoding="utf-8")
    background = STATIC_ROOT / "assets" / "voice-lab-login-bg.webp"

    assert "ui-refresh.css?v=20260826.3" in content
    assert 'url("../assets/voice-lab-login-bg.webp")' in stylesheet
    assert background.is_file()
    assert background.stat().st_size > 50_000


def test_generation_candidate_keeps_only_compact_selection_action() -> None:
    content = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_ROOT / "js" / "app.js").read_text(encoding="utf-8")
    stylesheet = (STATIC_ROOT / "css" / "workstation.css").read_text(encoding="utf-8")

    assert "workstation.css?v=20260826.16" in content
    assert "app.js?v=20260826.16" in content
    assert '<div class="candidate-media">${audio}${action}</div>' in javascript
    assert 'class="candidate-actions"' not in javascript
    assert "candidate.download_url" not in javascript
    assert ".candidate-media { display: grid;" in stylesheet


def test_generation_history_uses_responsive_card_grid() -> None:
    javascript = (STATIC_ROOT / "js" / "app.js").read_text(encoding="utf-8")
    stylesheet = (STATIC_ROOT / "css" / "workstation.css").read_text(encoding="utf-8")

    assert '<div class="line-history-grid">${records.join("")}</div>' in javascript
    assert ".line-history-grid { display: grid;" in stylesheet
    assert "repeat(auto-fit, minmax(min(100%, 520px), 1fr))" in stylesheet
    assert ".line-history-grid .line-history-candidates" in stylesheet
    assert 'class="icon-button line-history-delete"' in javascript
    assert 'aria-label="删除这条生成记录"' in javascript
    assert '${deleteButton}</div><div class="line-history-meta"' in javascript
    assert ".icon-button.line-history-delete svg" in stylesheet


def test_admin_jobs_show_named_project_voice_and_model_identities() -> None:
    content = (STATIC_ROOT / "admin.html").read_text(encoding="utf-8")
    javascript = (STATIC_ROOT / "js" / "admin.js").read_text(encoding="utf-8")

    assert "admin.js?v=20260826.2" in content
    assert '["任务", "项目", "人声", "模型", "状态", "提交时间"]' in javascript
    assert "identityCell(job.project_name, job.project_id)" in javascript
    assert "identityCell(job.voice_name, job.voice_id)" in javascript
    assert "identityCell(job.model_label, job.model_id)" in javascript
    assert "model.availability_reason" in javascript


def test_admin_health_rows_share_the_card_content_alignment() -> None:
    content = (STATIC_ROOT / "admin.html").read_text(encoding="utf-8")
    stylesheet = (STATIC_ROOT / "css" / "admin.css").read_text(encoding="utf-8")

    assert "admin.css?v=20260826.4" in content
    assert "padding: 10px 22px 14px;" in stylesheet
    assert "padding: 12px 0 0 17px;" in stylesheet
    assert ".health-summary > div" in stylesheet
    assert "border-bottom: 1px solid var(--line);" in stylesheet
