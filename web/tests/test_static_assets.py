from __future__ import annotations

import re
from pathlib import Path


WEB_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = WEB_ROOT / "static"
HTML_ASSET_RE = re.compile(r'(?:src|href)="(/(?:static|assets)/[^"?#]+)"')
MODULE_IMPORT_RE = re.compile(r'from\s+["\']([^"\']+)["\']')
HTML_ID_RE = re.compile(r'\bid=["\']([A-Za-z][\w-]*)["\']')
JS_ID_QUERY_RE = re.compile(r'\$\$?\(\s*["\']#([A-Za-z][\w-]*)["\']')


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
    sources = [*STATIC_ROOT.glob("*.html"), *(STATIC_ROOT / "js").rglob("*.js")]
    contents = [source.read_text(encoding="utf-8") for source in sources]
    declared = {match for content in contents for match in HTML_ID_RE.findall(content)}
    referenced = {
        match for content in contents for match in JS_ID_QUERY_RE.findall(content)
    }

    assert not sorted(referenced - declared)
