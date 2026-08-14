from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .database import Database
from .settings import Settings
from .storage import ensure_within


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Voice Lab 服务器本机原始音频管理")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="列出所有原始声音文件")
    action.add_argument("--delete-file", metavar="FILE_ID", help="删除指定原始声音文件")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = Settings.from_file()
    database = Database(settings.database_path)
    database.initialize()
    if args.list:
        rows = database.voices.list_all_files()
        for row in rows:
            state = "启用" if row["enabled"] else "禁用"
            print(
                f"{row['id']}  [{state}]  {row['voice_name']}  {row['original_name']}"
            )
            print(f"  {row['source_path']}")
        print(f"共 {len(rows)} 个原始声音文件")
        return 0

    record = database.voices.get_file(str(args.delete_file))
    if not record:
        print("找不到指定原始声音文件", file=sys.stderr)
        return 1
    path = ensure_within(Path(str(record["source_path"])), settings.root)
    if not args.yes:
        answer = input(f"确认永久删除 {record['original_name']}？输入 DELETE 继续: ").strip()
        if answer != "DELETE":
            print("已取消")
            return 0
    if path.is_file():
        path.unlink()
    database.voices.delete_file_record(str(record["id"]))
    print(f"已删除: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
