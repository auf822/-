"""Consistent backup of a live SQLite database, including its WAL contents."""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db import db_path

parser = argparse.ArgumentParser()
parser.add_argument('destination', nargs='?', default=f'data/backups/system-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.sqlite3')
args = parser.parse_args()
source = db_path().resolve()
target = Path(args.destination).resolve()
if not source.is_file():
    raise SystemExit('数据库不存在，请先初始化系统')
if target.exists():
    raise SystemExit('目标文件已存在，不覆盖已有备份')
target.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as original:
    with sqlite3.connect(target) as backup:
        original.backup(backup)
print(f'备份完成：{target}')
