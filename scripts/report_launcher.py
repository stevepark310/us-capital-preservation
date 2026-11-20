"""Local launcher for the CUBE reporting workflow. Requires an authenticated gh CLI."""
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from uuid import UUID

ORIGIN = "https://stevepark310.github.io"


class Launcher(BaseHTTPRequestHandler):
    def allowed(self):
        return (self.path == "/reports" and self.headers.get("Origin") == ORIGIN
                and self.headers.get("Host") == "127.0.0.1:41739")

    def reply(self, status):
        self.send_response(status)
        if self.allowed():
            self.send_header("Access-Control-Allow-Origin", ORIGIN)
            self.send_header("Access-Control-Allow-Methods", "POST")
            self.send_header("Access-Control-Allow-Headers", "X-Cube-Request")
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_OPTIONS(self):
        self.reply(204 if self.allowed() else 403)

    def do_POST(self):
        if not self.allowed():
            return self.reply(403)
        request_id = self.headers.get("X-Cube-Request", "")
        try:
            if str(UUID(request_id)) != request_id:
                raise ValueError
        except ValueError:
            return self.reply(400)
        try:
            result = subprocess.run(
                ["gh", "workflow", "run", "cube_pulse.yml", "--repo",
                 "stevepark310/us-capital-preservation", "--ref", "main",
                 "-f", "request_id=" + request_id],
                timeout=30, stdin=subprocess.DEVNULL, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return self.reply(502)
        self.reply(202 if result.returncode == 0 else 502)


if __name__ == "__main__":
    print("CUBE launcher: http://127.0.0.1:41739", flush=True)
    HTTPServer(("127.0.0.1", 41739), Launcher).serve_forever()
