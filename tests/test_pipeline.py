import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest.mock import patch

from local_transcriber.dashboard import make_server
from local_transcriber.queue import add_job, connect, export_index, update, worker_lock, write_json
from local_transcriber.sources import add_sources, fingerprint, youtube_url
from local_transcriber.worker import render_transcript, run

CONFIG = {"model": "small", "task": "transcribe", "language": "en",
          "device": "cpu", "compute_type": "int8", "threads": 2}


def cue(start=0, end=1, text="A synthetic test.", confidence=-.3, compression=1):
    return SimpleNamespace(start=start, end=end, text=text, avg_logprob=confidence, compression_ratio=compression)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.output = self.root / "output"
        self.db = connect(self.output)
        self.addCleanup(self.db.close)
        self.source = self.root / "Synthetic lesson.wav"
        self.source.write_bytes(b"synthetic media fixture")

    def add_local(self, **changes):
        config = {**CONFIG, **changes}
        return add_job(self.db, str(self.source), self.source.stem, "2026-01-15", 2,
                       config, "file:" + fingerprint(self.source))

    def test_repeated_inputs_deduplicate_but_different_settings_preserve_output(self):
        self.assertEqual(self.add_local(), 1)
        self.assertEqual(self.add_local(), 0)
        self.assertEqual(self.add_local(task="translate"), 1)
        rows = self.db.execute("SELECT folder FROM jobs").fetchall()
        self.assertEqual(len({row['folder'] for row in rows}), 2)

    def test_filenames_cannot_escape_output(self):
        add_job(self.db, "file", "../CON: bad / title ", "", None, CONFIG, "unique")
        row = self.db.execute("SELECT folder FROM jobs").fetchone()
        self.assertEqual((self.output / row["folder"]).resolve().parent, self.output)

    def test_local_folder_ignores_generated_output_and_deduplicates_content(self):
        self.assertEqual(add_sources(self.db, [str(self.root)], CONFIG, self.output), 1)
        duplicate = self.root / "duplicate.wav"
        duplicate.write_bytes(self.source.read_bytes())
        (self.output / "download.wav").write_bytes(b"temporary media")
        self.assertEqual(add_sources(self.db, [str(self.root)], CONFIG, self.output), 0)

    def test_bad_inputs_fail_clearly(self):
        with self.assertRaises(ValueError):
            add_sources(self.db, [str(self.root / "missing")], CONFIG, self.output)
        for value in ["https://youtube.com.evil.test/watch?v=abc", "file:///etc/passwd", "https://example.com/media"]:
            self.assertFalse(youtube_url(value))
        self.assertTrue(youtube_url("https://youtu.be/abcdefghijk"))

    @patch("yt_dlp.YoutubeDL")
    def test_youtube_playlist_deduplicates_ids_and_skips_live_broadcasts(self, youtube):
        client = youtube.return_value.__enter__.return_value
        client.extract_info.return_value = {"entries": [
            {"id": "video000001", "title": "Synthetic replay", "upload_date": "20260115", "duration": 30},
            {"id": "video000001", "title": "Duplicate replay"},
            {"id": "video000002", "title": "Still live", "live_status": "is_live"},
        ]}
        inputs = ["https://www.youtube.com/playlist?list=synthetic"]
        self.assertEqual(add_sources(self.db, inputs, CONFIG, self.output), 1)
        row = self.db.execute("SELECT source, date FROM jobs").fetchone()
        self.assertEqual(row["source"], "https://www.youtube.com/watch?v=video000001")
        self.assertEqual(row["date"], "2026-01-15")

    def test_parallel_index_updates_are_atomic(self):
        from concurrent.futures import ThreadPoolExecutor

        path = self.output / "index.json"
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda number: write_json(path, {"number": number}), range(20)))
        self.assertIn(json.loads(path.read_text())["number"], range(20))
        self.assertEqual(list(self.output.glob(".json-*")), [])

    def test_render_preserves_unicode_and_aligns_txt_and_srt(self):
        segments = [cue(text="A  synthetic\nlesson."), cue(.5, 2, "مرحبا", confidence=-1.2)]
        flags = render_transcript(segments, self.root, lambda _: None, translate=True)
        self.assertEqual((self.root / "transcript.txt").read_text(encoding="utf-8"),
                         "[00:00:00.000] A synthetic lesson.\n[00:00:01.000] مرحبا\n")
        self.assertIn("00:00:01,000 --> 00:00:02,000", (self.root / "subtitles.srt").read_text())
        self.assertEqual(len(flags), 2)
        self.assertEqual(json.loads((self.root / "review.json").read_text()), flags)

    def test_empty_audio_is_not_marked_complete(self):
        with self.assertRaisesRegex(ValueError, "No speech detected"):
            render_transcript([], self.root, lambda _: None)
        self.assertFalse((self.root / "transcript.txt").exists())

    def test_installed_audio_decoder_reads_a_real_wav(self):
        import wave
        from faster_whisper.audio import decode_audio

        fixture = self.root / "silence.wav"
        with wave.open(str(fixture), "wb") as recording:
            recording.setnchannels(1)
            recording.setsampwidth(2)
            recording.setframerate(16000)
            recording.writeframes(b"\0\0" * 16000)
        self.assertEqual(len(decode_audio(str(fixture))), 16000)

    @patch("faster_whisper.audio.decode_audio", return_value=[0] * 32000)
    @patch("faster_whisper.WhisperModel")
    def test_completed_jobs_are_skipped_and_no_audio_is_exported(self, model, decode):
        self.add_local()
        model.return_value.transcribe.return_value = ([cue()], SimpleNamespace(language="en"))
        self.assertEqual(run(self.db, self.output), 0)
        self.assertEqual(run(self.db, self.output), 0)
        self.assertEqual(model.return_value.transcribe.call_count, 1)
        row = self.db.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual(row["status"], "done")
        files = {p.name for p in (self.output / row["folder"]).iterdir()}
        self.assertEqual(files, {"subtitles.srt", "transcript.txt", "review.json", "metadata.json"})
        self.assertEqual(list(self.output.glob(".work-*")), [])
        self.assertTrue(self.source.exists())

    @patch("faster_whisper.audio.decode_audio", return_value=[0] * 32000)
    @patch("faster_whisper.WhisperModel")
    def test_interrupted_job_retries_then_skips_completed_work(self, model, decode):
        self.add_local()
        model.return_value.transcribe.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            run(self.db, self.output)
        self.assertEqual(self.db.execute("SELECT status FROM jobs").fetchone()[0], "interrupted")
        model.return_value.transcribe.side_effect = None
        model.return_value.transcribe.return_value = ([cue()], SimpleNamespace(language="en"))
        self.assertEqual(run(self.db, self.output), 0)
        self.assertEqual(self.db.execute("SELECT status FROM jobs").fetchone()[0], "done")

    @patch("faster_whisper.audio.decode_audio")
    @patch("faster_whisper.WhisperModel")
    def test_changed_source_cannot_be_attributed_to_old_job(self, model, decode):
        self.add_local()
        self.source.write_bytes(b"different content")
        self.assertEqual(run(self.db, self.output), 1)
        self.assertEqual(self.db.execute("SELECT status FROM jobs").fetchone()[0], "failed")
        decode.assert_not_called()

    @patch("faster_whisper.audio.decode_audio", return_value=[0] * 32000)
    @patch("faster_whisper.WhisperModel")
    def test_missing_output_is_rebuilt_when_resuming(self, model, decode):
        self.add_local()
        model.return_value.transcribe.side_effect = lambda *args, **kwargs: ([cue()], SimpleNamespace(language="en"))
        self.assertEqual(run(self.db, self.output), 0)
        folder = self.db.execute("SELECT folder FROM jobs").fetchone()[0]
        (self.output / folder / "subtitles.srt").unlink()
        self.assertEqual(run(self.db, self.output), 0)
        self.assertTrue((self.output / folder / "subtitles.srt").is_file())
        self.assertEqual(model.return_value.transcribe.call_count, 2)

    def test_failed_jobs_do_not_appear_as_downloadable_in_index(self):
        self.add_local()
        row = self.db.execute("SELECT id FROM jobs").fetchone()
        update(self.db, row["id"], status="failed", error="synthetic failure")
        export_index(self.db, self.output)
        index = json.loads((self.output / "index.json").read_text())
        self.assertEqual(index[0]["status"], "failed")
        self.assertNotIn("transcript", index[0])

    def test_worker_lock_excludes_a_second_process(self):
        import subprocess
        import sys

        with worker_lock(self.output):
            code = "from pathlib import Path; from local_transcriber.queue import worker_lock; " \
                   "ctx=worker_lock(Path(__import__('sys').argv[1])); ctx.__enter__()"
            result = subprocess.run([sys.executable, "-c", code, str(self.output)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"Another worker", result.stderr)

    def test_dashboard_serves_only_finished_artifacts_and_rejects_traversal(self):
        self.add_local()
        row = self.db.execute("SELECT id, folder FROM jobs").fetchone()
        folder = self.output / row["folder"]
        folder.mkdir()
        (folder / "transcript.txt").write_text("synthetic export")
        server = make_server(self.output, 0)
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}"
        for route in [f"/files/{row['id']}/transcript.txt", "/files/../queue.sqlite3", "/queue.sqlite3"]:
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(url + route)
        update(self.db, row["id"], status="done")
        with urllib.request.urlopen(url + f"/files/{row['id']}/transcript.txt") as response:
            self.assertEqual(response.read(), b"synthetic export")
        request = urllib.request.Request(url + "/api/jobs", headers={"Host": "attacker.example"})
        with self.assertRaises(urllib.error.HTTPError) as result:
            urllib.request.urlopen(request)
        self.assertEqual(result.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
