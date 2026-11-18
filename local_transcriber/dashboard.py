"""A read-only dashboard available only on this computer."""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .queue import connect

ASSETS = Path(__file__).parent / "assets"
ARTIFACTS = {"transcript.txt": "text/plain; charset=utf-8", "subtitles.srt": "text/plain; charset=utf-8",
             "review.json": "application/json", "metadata.json": "application/json"}


def make_server(output, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Host") not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
                self.send_error(403)
                return
            if self.path == "/":
                self.reply((ASSETS / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/display.woff2":
                self.reply((ASSETS / "display.woff2").read_bytes(), "font/woff2")
            elif self.path == "/api/jobs":
                db = connect(output)
                try:
                    fields = "id, title, date, duration, status, position, error, review_count"
                    items = [dict(row) for row in db.execute(f"SELECT {fields} FROM jobs ORDER BY date, title, id")]
                    self.reply(json.dumps(items).encode(), "application/json")
                finally:
                    db.close()
            elif match := re.fullmatch(r"/files/([a-f0-9]{20})/(transcript\.txt|subtitles\.srt|review\.json|metadata\.json)", self.path):
                db = connect(output)
                try:
                    row = db.execute("SELECT folder, status FROM jobs WHERE id = ?", (match[1],)).fetchone()
                finally:
                    db.close()
                if not row or row["status"] != "done":
                    self.send_error(404)
                    return
                path = (output / row["folder"] / match[2]).resolve()
                if output not in path.parents or not path.is_file():
                    self.send_error(404)
                    return
                self.reply(path.read_bytes(), ARTIFACTS[match[2]])
            else:
                self.send_error(404)

        def reply(self, body, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
