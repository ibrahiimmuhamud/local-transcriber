"""One local worker with resumable jobs and atomic transcript exports."""
import json
import hashlib
import re
import shutil
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from .queue import export_index, update, worker_lock, write_json
from .sources import download, fingerprint, youtube_url


def timestamp(milliseconds, separator=","):
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, fraction = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{fraction:03d}"


def render_transcript(segments, directory, progress, translate=False):
    cues, lines, flags = [], [], []
    last_end = 0
    for segment in segments:
        words = " ".join(segment.text.split())
        progress(segment.end)
        if not words:
            continue
        start = max(last_end, round(segment.start * 1000))
        end = max(start + 1, round(segment.end * 1000))
        if segment.avg_logprob < -1:
            flags.append({"seconds": start / 1000, "reason": "Low model confidence"})
        if segment.compression_ratio > 2.4:
            flags.append({"seconds": start / 1000, "reason": "Possible repetition"})
        if translate and re.search(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]", words):
            flags.append({"seconds": start / 1000, "reason": "English translation contains Arabic script"})
        cues.append(f"{len(cues) + 1}\n{timestamp(start)} --> {timestamp(end)}\n{words}\n")
        lines.append(f"[{timestamp(start, '.')}] {words}")
        last_end = end
    if not cues:
        raise ValueError("No speech detected. Check that this file contains audible speech.")
    (directory / "subtitles.srt").write_text("\n".join(cues), encoding="utf-8")
    (directory / "transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(directory / "review.json", flags)
    return flags


def run(db, output):
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio

    failures = 0
    models = {}
    with worker_lock(output):
        for row in db.execute("SELECT id, folder FROM jobs WHERE status = 'done'").fetchall():
            if not all((output / row["folder"] / name).is_file()
                       for name in ("transcript.txt", "subtitles.srt", "review.json", "metadata.json")):
                update(db, row["id"], status="queued", error="An output file is missing. Rebuilding this recording.")
        db.execute("UPDATE jobs SET status = 'queued', position = 0 WHERE status != 'done'")
        db.commit()
        # Interrupted downloads and incomplete exports are disposable, completed folders are retained.
        for stale in output.glob(".work-*"):
            if stale.is_dir() and not stale.is_symlink():
                shutil.rmtree(stale)
        export_index(db, output)
        for item in db.execute("SELECT * FROM jobs WHERE status != 'done' ORDER BY date, title, id").fetchall():
            key = item["id"]
            config = json.loads(item["settings"])
            destination = output / item["folder"]
            print(f"Transcribing {item['title']}", flush=True)
            try:
                with TemporaryDirectory(prefix=".work-", dir=output) as scratch:
                    stage = Path(scratch)
                    if youtube_url(item["source"]):
                        update(db, key, status="downloading", error="", position=0)
                        source = download(item["source"], stage)
                    else:
                        source = Path(item["source"])
                        # Reject replaced input files rather than attributing new speech to an old job.
                        identity = "file:" + fingerprint(source)
                        expected = hashlib.sha256((identity + "\n" + item["settings"]).encode()).hexdigest()[:20]
                        if expected != key:
                            raise ValueError("The source file changed. Add this file again to create a new job.")
                    update(db, key, status="loading model", error="", position=0)
                    model_key = (config["model"], config["device"], config["compute_type"], config["threads"])
                    if model_key not in models:
                        models[model_key] = WhisperModel(config["model"], device=config["device"],
                            compute_type=config["compute_type"], cpu_threads=config["threads"])
                    model = models[model_key]
                    audio = decode_audio(str(source), sampling_rate=16000)
                    duration = len(audio) / 16000
                    if not duration:
                        raise ValueError("This recording is empty")
                    update(db, key, status="transcribing", duration=duration)
                    segments, info = model.transcribe(audio, task=config["task"], language=config["language"],
                        multilingual=config["language"] is None, beam_size=5, vad_filter=True,
                        condition_on_previous_text=False, vad_parameters={"min_silence_duration_ms": 600})
                    last_update = 0

                    def progress(position):
                        nonlocal last_update
                        if time.monotonic() - last_update >= 2:
                            update(db, key, position=min(position, duration))
                            print(f"  {position / 60:.1f} / {duration / 60:.1f} minutes", flush=True)
                            last_update = time.monotonic()

                    result = stage / "result"
                    result.mkdir()
                    flags = render_transcript(segments, result, progress, translate=config["task"] == "translate")
                    metadata = {"id": key, "title": item["title"], "date": item["date"],
                                "duration": duration, "settings": config, "detected_language": info.language,
                                "review_count": len(flags), "source_kind": "youtube" if youtube_url(item["source"]) else "file"}
                    write_json(result / "metadata.json", metadata)
                    if destination.exists():
                        # A crash may leave the final folder saved before its database commit.
                        saved = json.loads((destination / "metadata.json").read_text(encoding="utf-8"))
                        if saved["id"] != key or saved["settings"] != config:
                            raise ValueError("An unrelated output folder already exists")
                        old = stage / "previous-result"
                        destination.rename(old)
                        try:
                            result.rename(destination)
                        except BaseException:
                            old.rename(destination)
                            raise
                    else:
                        result.rename(destination)
                    update(db, key, status="done", position=duration, error="", review_count=len(flags))
                    print(f"Saved {destination.name} ({len(flags)} review notes)", flush=True)
            except KeyboardInterrupt:
                update(db, key, status="interrupted", error="Stopped. Run the queue again to resume.")
                raise
            except Exception as error:
                failures += 1
                update(db, key, status="failed", error=str(error)[:1000])
                print(f"Failed: {item['title']}: {error}", flush=True)
            finally:
                export_index(db, output)
    return failures
