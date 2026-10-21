"""Family Safety database migration (additive and reversible).

The server creates missing tables itself at start (SQLModel create_all), so `up` is only needed to prepare a
database by hand. `down` removes ONLY the Family Safety tables (fs_*) after a backup copy; users, families, alerts
and every other VoiceGuard table are not touched. Stop the server before running `down`.

  .venv\\Scripts\\python scripts\\family_safety_migrate.py status
  .venv\\Scripts\\python scripts\\family_safety_migrate.py up
  .venv\\Scripts\\python scripts\\family_safety_migrate.py down            (backup first, then drop fs_* tables)
  .venv\\Scripts\\python scripts\\family_safety_migrate.py down --purge-alerts   (also delete family_* alerts)

To switch the feature off WITHOUT deleting anything, set VG_FAMILY_SAFETY=0 in backend/.env and restart.
"""
import argparse
import datetime as dt
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app import config, family_safety as fs  # noqa: E402


def _engine(db: str):
    return create_engine(f"sqlite:///{db}")


def tables_in(db: str) -> set[str]:
    con = sqlite3.connect(db)
    try:
        return {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    finally:
        con.close()


def up(db: str) -> list[str]:
    SQLModel.metadata.create_all(_engine(db), tables=[t.__table__ for t in fs.TABLES])
    return sorted(t for t in tables_in(db) if t.startswith("fs_"))


def down(db: str, backup: bool = True, purge_alerts: bool = False) -> str | None:
    copy = None
    if backup:
        out = Path(config.DATA_DIR) / "backups"
        out.mkdir(parents=True, exist_ok=True)
        copy = str(out / f"voiceguard-before-family-safety-rollback-{dt.datetime.now():%Y%m%d-%H%M%S}.db")
        shutil.copy2(db, copy)
    SQLModel.metadata.drop_all(_engine(db), tables=[t.__table__ for t in reversed(fs.TABLES)])
    if purge_alerts:
        con = sqlite3.connect(db)
        con.execute("delete from alert where kind like 'family\\_%' escape '\\'")
        con.commit()
        con.close()
    return copy


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["status", "up", "down"])
    ap.add_argument("--db", default=str(config.DB_PATH))
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--purge-alerts", action="store_true")
    a = ap.parse_args()
    if a.action == "status":
        have = sorted(t for t in tables_in(a.db) if t.startswith("fs_"))
        want = sorted(t.__tablename__ for t in fs.TABLES)
        print("Family Safety tables present:", ", ".join(have) or "none")
        print("missing:", ", ".join(sorted(set(want) - set(have))) or "none")
    elif a.action == "up":
        print("created / present:", ", ".join(up(a.db)))
    else:
        copy = down(a.db, backup=not a.no_backup, purge_alerts=a.purge_alerts)
        print("Family Safety tables removed." + (f" Backup: {copy}" if copy else ""))


if __name__ == "__main__":
    main()
