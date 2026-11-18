"""Command-line entry point."""
import argparse
import os
import sys
import time
from threading import Thread

from . import __version__
from .queue import connect, export_index


def main(argv=None):
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Transcribe recordings locally with Whisper. Re-run to resume a batch.")
    parser.add_argument("inputs", nargs="*", help="Media files, folders, or authorized YouTube video/playlist URLs")
    parser.add_argument("--output", type=Path, default=Path("transcripts"), help="Output and queue folder (default: transcripts)")
    parser.add_argument("--task", choices=("transcribe", "translate"), default="transcribe",
                        help="Keep the spoken language or translate speech to English")
    parser.add_argument("--model", default="large-v3", help="Whisper model name or local model directory (default: large-v3)")
    parser.add_argument("--language", help="Source language code, e.g. en or ar. Omit for automatic detection.")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--compute-type", default="int8", help="CTranslate2 compute type (default: int8)")
    parser.add_argument("--threads", type=int, default=min(8, os.cpu_count() or 4))
    parser.add_argument("--dashboard", action="store_true", help="Show a local progress dashboard and keep it open after processing")
    parser.add_argument("--serve-only", action="store_true", help="View an existing output folder without running the worker")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    if args.threads < 1 or not 1 <= args.port <= 65535:
        parser.error("Threads must be positive and port must be between 1 and 65535")
    if args.serve_only and args.inputs:
        parser.error("--serve-only displays existing jobs. Remove it to add recordings.")
    output = args.output.expanduser().resolve()
    db = connect(output)
    server = None
    try:
        if args.inputs:
            from .sources import add_sources

            config = {"model": args.model, "task": args.task, "language": args.language,
                      "device": args.device, "compute_type": args.compute_type, "threads": args.threads}
            added = add_sources(db, args.inputs, config, output)
            print(f"Added {added} recordings. Queue saved in {output}", flush=True)
        if not db.execute("SELECT count(*) FROM jobs").fetchone()[0] and not args.serve_only:
            parser.error("Add at least one media file, folder, or YouTube video/playlist URL")
        export_index(db, output)
        if args.dashboard or args.serve_only:
            from .dashboard import make_server

            server = make_server(output, args.port)
            Thread(target=server.serve_forever, daemon=True).start()
            print(f"Dashboard: http://127.0.0.1:{args.port} (Ctrl+C to close)", flush=True)
        failures = 0
        if not args.serve_only:
            from .worker import run

            failures = run(db, output)
            print(f"Batch finished with {failures} failures. Re-run the same command to retry failed jobs.", flush=True)
        if server:
            print("Dashboard remains open. Ctrl+C closes it.", flush=True)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        return int(failures > 0)
    except KeyboardInterrupt:
        print("\nStopped. Re-run this output folder to resume the batch.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    finally:
        if server:
            server.shutdown()
            server.server_close()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
