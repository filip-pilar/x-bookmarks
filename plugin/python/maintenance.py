"""Local backup and restore with the library service stopped."""

import argparse, contextlib, fcntl, json, os, shutil, sqlite3, tempfile
from pathlib import Path
from store import HOME, atomic, db
from sources import checksum, reserve_space


def database_snapshot(path):
    path = Path(path)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".building.sqlite")
    with (
        contextlib.closing(sqlite3.connect(HOME / "library.sqlite")) as source,
        contextlib.closing(sqlite3.connect(temporary)) as target,
    ):
        source.backup(target)
    os.replace(temporary, path)


def inventory_snapshot(path):
    path = Path(path)
    if path.exists():
        return
    # Reconciliation changes only inventory tables, not retained content or vectors.
    with db() as c:
        c.execute("BEGIN")
        snapshot = {
            "items": [
                dict(r) for r in c.execute("SELECT id,membership,seen_run,folder_checked_at FROM items")
            ],
            "folders": [dict(r) for r in c.execute("SELECT * FROM folders")],
            "members": [dict(r) for r in c.execute("SELECT * FROM members")],
            "settings": [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM settings WHERE key IN ('last_inventory','full_inventory_at','folder_inventory')"
                )
            ],
        }
    atomic(path, snapshot)


def backup(destination):
    destination = Path(destination).expanduser().resolve()
    if (
        destination.exists()
        or destination == HOME.resolve()
        or HOME.resolve() in destination.parents
    ):
        raise ValueError("Choose a new backup directory outside the library.")
    files = [
        p
        for name in ("items", "sources", "scans")
        for p in (HOME / name).rglob("*")
        if p.is_file()
    ]
    parent = destination.parent
    while not parent.exists():
        parent = parent.parent
    reserve_space(
        sum(p.stat().st_size for p in files) + (HOME / "library.sqlite").stat().st_size,
        parent,
    )
    destination.mkdir(parents=True, mode=0o700)
    database_snapshot(destination / "library.sqlite")
    for name in ("items", "sources", "scans"):
        if (HOME / name).exists():
            shutil.copytree(HOME / name, destination / name)
    for name in ("config.json", "runtime.json"):
        if (HOME / name).exists():
            shutil.copy2(HOME / name, destination / name)
    atomic(destination / "backup-info.json", {"library": str(HOME.resolve())})
    manifest = {
        str(p.relative_to(destination)): checksum(p)
        for p in destination.rglob("*")
        if p.is_file()
    }
    atomic(destination / "manifest.json", manifest)
    return {
        "backup": str(destination),
        "files": len(manifest),
        "credentials_included": False,
    }


def restore(source):
    source = Path(source).expanduser().resolve()
    manifest = json.loads((source / "manifest.json").read_text())
    actual = {
        str(p.relative_to(source))
        for p in source.rglob("*")
        if p.is_file() and p != source / "manifest.json"
    }
    if actual != set(manifest) or any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("Backup inventory changed; library not changed.")
    if json.loads((source / "backup-info.json").read_text()).get("library") != str(
        HOME.resolve()
    ):
        raise ValueError(
            "Restore to the original library directory to preserve evidence paths."
        )
    for name, expected in manifest.items():
        path = (source / name).resolve()
        if source not in path.parents or checksum(path) != expected:
            raise ValueError("Backup path or checksum is invalid; library not changed.")
    with sqlite3.connect(source / "library.sqlite") as c:
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup database is invalid.")
    c.close()
    reserve_space(sum((source / name).stat().st_size for name in manifest))
    # Prepare every replacement before changing the library. Retain the displaced files.
    stage = Path(tempfile.mkdtemp(prefix="restore-", dir=HOME.parent))
    shutil.copytree(source, stage / "data")
    previous = stage / "previous"
    previous.mkdir()
    with sqlite3.connect(stage / "data/library.sqlite") as c:
        c.execute("PRAGMA journal_mode=DELETE")
        c.execute("INSERT OR REPLACE INTO settings VALUES ('paused','true')")
        c.execute("UPDATE jobs SET state='queued' WHERE state='running'")
    c.close()
    for name in (
        "items",
        "sources",
        "scans",
        "library.sqlite",
        "config.json",
        "runtime.json",
    ):
        if (HOME / name).exists():
            os.replace(HOME / name, previous / name)
        if (stage / "data" / name).exists():
            os.replace(stage / "data" / name, HOME / name)
    for suffix in ("-wal", "-shm"):
        path = HOME / ("library.sqlite" + suffix)
        if path.exists():
            os.replace(path, previous / path.name)
    return {"restored": True, "paused": True, "previous_files": str(previous)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("path")
    args = parser.parse_args()
    with (HOME / "service.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(
                "Stop the Bookmark Library service before backup or restore."
            )
        print(
            json.dumps(
                backup(args.path) if args.action == "backup" else restore(args.path)
            )
        )
