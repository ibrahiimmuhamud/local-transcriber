"""Persistent jobs and safe output paths, shared by the worker and dashboard."""
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

MEDIA_EXTENSIONS = {".mp3", ".mp4", ".m4a", ".wav", ".webm", ".flac", ".ogg", ".mkv", ".mov", ".aiff", ".aif"}


def connect(output):
    output.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(output / "queue.sqlite3", timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL,
        date TEXT NOT NULL, duration REAL, settings TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued', position REAL NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '', folder TEXT NOT NULL, review_count INTEGER NOT NULL DEFAULT 0
    )""")
    db.commit()
    return db


def add_job(db, source, title, date, duration, settings, identity):
    config = json.dumps(settings, sort_keys=True)
    key = hashlib.sha256((identity + "\n" + config).encode()).hexdigest()[:20]
    title_slug = re.sub(r'[^\w .()-]', "-", title, flags=re.UNICODE).strip(" .-") or "Recording"
    # Keep paths usable on Windows and on filesystems with byte-based filename limits.
    title_slug = title_slug.encode()[:100].decode(errors="ignore")
    folder = f"{date or 'Undated'} - {title_slug} [{key}]"
    cursor = db.execute("""INSERT OR IGNORE INTO jobs
        (id, source, title, date, duration, settings, folder) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (key, source, title, date, duration, config, folder))
    db.commit()
    return cursor.rowcount


def update(db, key, **changes):
    allowed = {"status", "position", "error", "duration", "review_count"}
    if not changes or not changes.keys() <= allowed:
        raise ValueError("Invalid job update")
    columns = ", ".join(f"{column} = ?" for column in changes)
    db.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*changes.values(), key))
    db.commit()


@contextmanager
def worker_lock(output):
    path = output / "worker.lock"
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.write(b"\0")
            handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("Another worker is using this output folder") from error
        else:
            import fcntl

            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("Another worker is using this output folder") from error
        yield


def write_json(path, value):
    body = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    file = NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".json-", delete=False)
    pending = Path(file.name)
    try:
        with file:
            file.write(body)
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def export_index(db, output):
    items = []
    for row in db.execute("SELECT * FROM jobs ORDER BY date, title, id"):
        item = {key: row[key] for key in ("id", "title", "date", "duration", "status", "review_count")}
        if row["status"] == "done":
            item["transcript"] = f"{row['folder']}/transcript.txt"
            item["subtitles"] = f"{row['folder']}/subtitles.srt"
        items.append(item)
    write_json(output / "index.json", items)
