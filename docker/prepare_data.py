from __future__ import annotations

"""Create an isolated, Docker-readable copy of the existing Voice Lab data.

This command is intentionally separate from the deployment BAT.  It copies the
current Windows data once, rewrites persisted paths to the paths used by the
container, and leaves the original ``web/data`` untouched.
"""

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path


PATH_COLUMNS: tuple[tuple[str, str], ...] = (
    ("voice_files", "source_path"),
    ("scripts", "source_path"),
    ("job_items", "audio_path"),
    ("job_items", "raw_audio_path"),
    ("job_item_candidates", "audio_path"),
    ("job_item_candidates", "raw_audio_path"),
    ("job_variant_items", "audio_path"),
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=root)
    parser.add_argument("--destination", type=Path, default=root / "docker-data")
    parser.add_argument(
        "--allow-existing",
        action="store_true",
        help="允许写入已有的目标目录；默认拒绝以免误覆盖 Docker 数据",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_root = args.source_root.resolve()
    destination = args.destination.resolve()
    source_data = source_root / "web" / "data"
    source_database = source_data / "voice_web.sqlite3"

    _validate_source(source_root, source_data, source_database)
    if destination.exists() and not args.allow_existing:
        raise RuntimeError(
            f"目标已存在，为避免覆盖 Docker 数据而停止: {destination}\n"
            "如确认要重新制作副本，请先备份后删除目标，或显式使用 --allow-existing。"
        )

    if destination.exists():
        _ensure_destination_is_scoped(destination, source_root)
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    _copy_data_tree(source_data, destination)
    _backup_database(source_database, destination / "voice_web.sqlite3")
    report = _rewrite_database(source_root, destination)
    marker = {
        "schema": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "source_root": str(source_root),
        "container_root": "/app",
        "database": "voice_web.sqlite3",
        "path_columns": report,
    }
    (destination / ".migration.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Docker 数据副本已创建: {destination}")
    for key, value in report.items():
        print(f"{key}: {value}")
    print("原始 web/data 未修改；部署 BAT 不会再次执行此迁移。")
    return 0


def _validate_source(root: Path, data: Path, database: Path) -> None:
    if not root.is_dir():
        raise RuntimeError(f"源工作区不存在: {root}")
    if not data.is_dir():
        raise RuntimeError(f"源数据目录不存在: {data}")
    if not database.is_file():
        raise RuntimeError(f"源数据库不存在: {database}")


def _ensure_destination_is_scoped(destination: Path, root: Path) -> None:
    expected = root.resolve() / "docker-data"
    if destination != expected:
        raise RuntimeError("--allow-existing 只允许重建工作区根目录下的 docker-data")


def _copy_data_tree(source: Path, destination: Path) -> None:
    ignored = shutil.ignore_patterns(
        "voice_web.sqlite3",
        "voice_web.sqlite3-wal",
        "voice_web.sqlite3-shm",
    )
    for child in source.iterdir():
        if child.name in {
            "voice_web.sqlite3",
            "voice_web.sqlite3-wal",
            "voice_web.sqlite3-shm",
        }:
            continue
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, ignore=ignored)
        else:
            shutil.copy2(child, target)


def _backup_database(source: Path, destination: Path) -> None:
    temporary = destination.with_suffix(".sqlite3.tmp")
    temporary.unlink(missing_ok=True)
    source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(temporary)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()
    temporary.replace(destination)


def _rewrite_database(
    source_root: Path, destination: Path
) -> dict[str, dict[str, int]]:
    database = destination / "voice_web.sqlite3"
    imported_root = destination / "imported"
    report: dict[str, dict[str, int]] = {}
    with sqlite3.connect(database) as connection:
        for table, column in PATH_COLUMNS:
            values = connection.execute(
                f"SELECT rowid, {column} FROM {table} WHERE {column} <> ''"
            ).fetchall()
            changed = 0
            missing = 0
            for rowid, value in values:
                rewritten = _rewrite_path(
                    str(value), source_root, destination, imported_root
                )
                if rewritten != str(value):
                    connection.execute(
                        f"UPDATE {table} SET {column}=? WHERE rowid=?",
                        (rewritten, rowid),
                    )
                    changed += 1
                if not _container_path_exists(rewritten, source_root, destination):
                    missing += 1
            report[f"{table}.{column}"] = {
                "total": len(values),
                "rewritten": changed,
                "missing_after_mapping": missing,
            }
        connection.commit()
    return report


def _rewrite_path(
    value: str,
    source_root: Path,
    destination: Path,
    imported_root: Path,
) -> str:
    normalized = value.replace("\\", "/")
    source_text = source_root.as_posix().rstrip("/")
    relative: str | None = None
    if normalized.casefold() == source_text.casefold():
        relative = ""
    elif normalized.casefold().startswith(source_text.casefold() + "/"):
        relative = normalized[len(source_text) + 1 :]
    if relative is None:
        source_path = Path(value)
        if not source_path.is_file():
            return value
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        target_directory = imported_root / digest
        target_directory.mkdir(parents=True, exist_ok=True)
        target = target_directory / source_path.name
        if not target.exists():
            shutil.copy2(source_path, target)
        return "/app/web/data/imported/" + digest + "/" + source_path.name
    parts = [part for part in relative.split("/") if part]
    if not parts:
        return "/app"
    if (
        parts[0].casefold() == "web"
        and len(parts) > 1
        and parts[1].casefold() == "data"
    ):
        return "/app/web/data/" + "/".join(parts[2:])
    if parts[0].casefold() in {"input", "output"}:
        return "/app/" + "/".join(parts)
    source_path = source_root.joinpath(*parts)
    if not source_path.is_file():
        return "/app/" + "/".join(parts)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    target_directory = imported_root / digest
    target_directory.mkdir(parents=True, exist_ok=True)
    target = target_directory / source_path.name
    if not target.exists():
        shutil.copy2(source_path, target)
    return "/app/web/data/imported/" + digest + "/" + source_path.name


def _container_path_exists(value: str, source_root: Path, destination: Path) -> bool:
    if value.startswith("/app/web/data/"):
        return (destination / value.removeprefix("/app/web/data/")).is_file()
    if value.startswith("/app/input/"):
        return (source_root / value.removeprefix("/app/")).is_file()
    if value.startswith("/app/output/"):
        return (source_root / value.removeprefix("/app/")).is_file()
    return False


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, sqlite3.Error) as error:
        print(f"迁移失败: {error}", file=sys.stderr)
        raise SystemExit(1) from error
