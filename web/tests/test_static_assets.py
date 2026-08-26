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
