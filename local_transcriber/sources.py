"""Discover local media or user-specified YouTube videos and playlists."""
import hashlib
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .queue import MEDIA_EXTENSIONS, add_job


def youtube_url(value):
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower()
        return url.scheme in {"https", "http"} and host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
    except ValueError:
        return False


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def add_sources(db, inputs, settings, output):
    added = 0
    from yt_dlp import YoutubeDL

    for value in inputs:
        if youtube_url(value):
            # Inventory metadata first. Downloading happens one audio file at a time.
            with YoutubeDL({"extract_flat": "in_playlist", "skip_download": True, "quiet": True}) as client:
                info = client.extract_info(value, download=False)
                entries = info.get("entries", (info,))
                for entry in entries:
                    if not entry or entry.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
                        continue
                    video_id = entry.get("id", "")
                    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                        raise ValueError("Use a YouTube video or playlist URL. Nested channel pages are unsupported.")
                    source = f"https://www.youtube.com/watch?v={video_id}"
                    stamp = entry.get("release_date") or entry.get("upload_date") or ""
                    date = datetime.strptime(stamp, "%Y%m%d").date().isoformat() if stamp else ""
                    added += add_job(db, source, entry.get("title") or video_id, date,
                                     entry.get("duration"), settings, f"youtube:{video_id}")
        else:
            path = Path(value).expanduser().resolve()
            if not path.exists():
                raise ValueError(f"Input does not exist or is not a supported YouTube URL: {value}")
            files = sorted(path.rglob("*")) if path.is_dir() else [path]
            matched = 0
            for item in files:
                if not item.is_file() or item.suffix.lower() not in MEDIA_EXTENSIONS:
                    continue
                if output == item or output in item.parents:
                    continue
                matched += 1
                date = datetime.fromtimestamp(item.stat().st_mtime).date().isoformat()
                added += add_job(db, str(item), item.stem, date, None, settings, f"file:{fingerprint(item)}")
            if not matched:
                raise ValueError(f"No supported audio or video files found in {path}")
    return added


def download(source, directory):
    from yt_dlp import YoutubeDL

    options = {"format": "bestaudio[ext=m4a]/bestaudio", "noplaylist": True, "quiet": True,
               "outtmpl": str(directory / "audio.%(ext)s"), "socket_timeout": 20,
               "retries": 2, "fragment_retries": 2,
               "match_filter": lambda info, **_: "Wait for a complete replay" if info.get("live_status")
                   in {"is_live", "is_upcoming", "post_live"} else None}
    with YoutubeDL(options) as client:
        info = client.extract_info(source, download=True)
        if info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
            raise ValueError("Wait until this livestream has a complete replay")
    matches = [path for path in directory.iterdir() if path.suffix.lower() in MEDIA_EXTENSIONS]
    if len(matches) != 1:
        raise ValueError("The download did not produce exactly one audio recording")
    return matches[0]
