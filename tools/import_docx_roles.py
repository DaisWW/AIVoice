"""Replace one project's scripts/jobs with role-split scripts from a DOCX."""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


RUNTIME_DATA_ROOT = Path("/app/web/data")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff._-]+")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_sections(document_path: Path):
    import sys

    repo_root = _repo_root()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    web_root = _repo_root() / "web"
    if str(web_root) not in sys.path:
        sys.path.insert(0, str(web_root))
    from app.script_parser import parse_docx_sections

    return parse_docx_sections(document_path.read_bytes())


def _safe_name(value: str) -> str:
    name = SAFE_NAME_RE.sub("_", value).strip(" .")
    return name or "role"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_role_file(path: Path, items) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("text", "pronunciation"))
        writer.writerows((item.text, item.pronunciation) for item in items)
    temporary.replace(path)


def _local_path(runtime_path: str, data_root: Path) -> Path:
    runtime_root = RUNTIME_DATA_ROOT.as_posix().rstrip("/")
    if not runtime_path.startswith(runtime_root + "/"):
        raise ValueError(f"源文件不在运行时数据目录内: {runtime_path}")
    relative = runtime_path[len(runtime_root) + 1 :]
    path = (data_root / relative).resolve()
    root = data_root.resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"源文件超出数据目录: {path}")
    return path


def _remove_job_files(data_root: Path, job_ids: list[str]) -> None:
    root = data_root.resolve()
    for job_id in job_ids:
        directory = (data_root / "jobs" / job_id).resolve()
        if directory != root and root not in directory.parents:
            raise ValueError(f"任务目录超出数据目录: {directory}")
        if directory.is_dir():
            shutil.rmtree(directory)
        for suffix in (".zip", "-accepted.zip"):
            archive = (data_root / "exports" / f"{job_id}{suffix}").resolve()
            if archive != root and root not in archive.parents:
                raise ValueError(f"任务导出文件超出数据目录: {archive}")
            archive.unlink(missing_ok=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--voice-id", required=True)
    parser.add_argument("--delete-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    document = args.document.resolve()
    database_path = args.database.resolve()
    data_root = args.data_root.resolve()
    if not document.is_file() or not database_path.is_file():
        raise SystemExit("文档或数据库不存在")
    sections = _load_sections(document)
    if not sections:
        raise SystemExit("DOCX 没有可导入的角色台词")

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        project = connection.execute(
            "SELECT id, owner_id FROM projects WHERE id=? AND status='active'",
            (args.project_id,),
        ).fetchone()
        voice = connection.execute(
            "SELECT id FROM voices WHERE id=? AND project_id=?",
            (args.voice_id, args.project_id),
        ).fetchone()
        enabled_files = connection.execute(
            "SELECT COUNT(*) FROM voice_files WHERE voice_id=? AND enabled=1",
            (args.voice_id,),
        ).fetchone()[0]
        if not project or not voice or not enabled_files:
            raise SystemExit("项目或默认声音不存在，或声音没有启用录音")

        old_scripts = connection.execute(
            "SELECT id, source_path FROM scripts WHERE project_id=?",
            (args.project_id,),
        ).fetchall()
        old_script_ids = [str(row["id"]) for row in old_scripts]
        job_rows = connection.execute(
            """
            SELECT DISTINCT id FROM jobs
            WHERE project_id=? OR script_id IN ({})
            """.format(",".join("?" for _ in old_script_ids) or "NULL"),
            (args.project_id, *old_script_ids),
        ).fetchall()
        job_ids = [str(row["id"]) for row in job_rows]
        print(f"角色台本: {len(sections)} 份, 台词: {sum(len(v) for v in sections.values())} 段")
        print(f"将删除: {len(old_scripts)} 份旧台本, {len(job_ids)} 条生成记录")
        if args.dry_run:
            return
        if not args.delete_existing:
            raise SystemExit("需要显式传入 --delete-existing 才会修改历史数据")

        old_paths = [_local_path(str(row["source_path"]), data_root) for row in old_scripts]
        timestamp = _now()
        imported_root = data_root / "uploads" / "scripts" / args.project_id / "docx-roles"
        rows = []
        new_paths: set[Path] = set()
        for index, (role, items) in enumerate(sections.items(), start=1):
            script_id = f"script-{uuid.uuid4().hex[:12]}"
            filename = f"{index:03d}_{_safe_name(role)}.csv"
            local_path = imported_root / filename
            _write_role_file(local_path, items)
            new_paths.add(local_path.resolve())
            runtime_path = PurePosixPath(
                RUNTIME_DATA_ROOT.as_posix(),
                "uploads",
                "scripts",
                args.project_id,
                "docx-roles",
                filename,
            )
            rows.append(
                (
                    script_id,
                    role[:80],
                    filename,
                    runtime_path.as_posix(),
                    str(project["owner_id"]),
                    args.project_id,
                    "docx-role",
                    args.voice_id,
                    None,
                    len(items),
                    timestamp,
                )
            )

        with connection:
            if job_ids:
                connection.executemany("DELETE FROM jobs WHERE id=?", [(job_id,) for job_id in job_ids])
            if old_script_ids:
                connection.executemany(
                    "DELETE FROM scripts WHERE id=?", [(script_id,) for script_id in old_script_ids]
                )
            connection.executemany(
                """
                INSERT INTO scripts(
                    id, name, original_name, source_path, owner_id, project_id,
                    source_kind, default_voice_id, default_effect_id, item_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        for path in old_paths:
            if path.resolve() not in new_paths:
                path.unlink(missing_ok=True)
        _remove_job_files(data_root, job_ids)
        print(f"已导入: {len(rows)} 份角色台本, {sum(len(v) for v in sections.values())} 段")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
