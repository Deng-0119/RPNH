"""Loopback HTTP UI with two independently bound clients and gated stock Viewer."""
import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from cpn.frontend.dashboard import RegistryDashboard
from cpn.frontend.server import handle_request
from cpn.rpnh.registry.event_store import RegistryConflict
from .app import ApprovalApp, catalog
from .host import registration

STATIC = Path(__file__).with_name("static")


class ApprovalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, app, port=0):
        self.app = app
        # Per-process controlled demo access only. No account service, role picker,
        # credential endpoint, persistent credential file or browser authority JSON.
        self.passwords = {label: secrets.token_urlsafe(24) for label in ("requester", "approver", "unrelated")}
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = "http://127.0.0.1:" + str(self.server_port)
        inventory = catalog()
        registration().bind_schema_catalog(inventory)
        self.viewer = RegistryDashboard(app.directory / "run", catalog=inventory)

    def session(self, authorization):
        if not authorization or not authorization.startswith("Basic "):
            raise PermissionError("use your separately supplied controlled-client login")
        try:
            user, password = base64.b64decode(authorization[6:], validate=True).decode().split(":", 1)
        except (ValueError, UnicodeError):
            raise PermissionError("invalid client binding") from None
        expected = self.passwords.get(user)
        if expected is None or not secrets.compare_digest(password, expected):
            raise PermissionError("invalid client binding")
        return self.app.sessions[user]


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type="application/json; charset=utf-8", headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        fields = {"Content-Type": content_type, "Content-Length": str(len(body)),
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; worker-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; frame-ancestors 'self'"}
        fields.update(headers or {})
        for key, value in fields.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _handle(self):
        try:
            if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
                raise PermissionError("unexpected Host")
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise PermissionError("cross-site request rejected")
            if self.command == "POST" and self.headers.get("Origin") != self.server.origin:
                raise PermissionError("POST requires exact local Origin")
            session = self.server.session(self.headers.get("Authorization"))
            app = self.server.app
            # Read fence precedes state, assets, source observations and Viewer APIs.
            app._require(session, ("requester", "approver"))
            parsed = urlsplit(self.path)
            path = parsed.path
            if self.command == "POST":
                if path not in {"/api/request", "/api/decision"} or parsed.query:
                    self._send(404, {"error": "unknown action"}); return
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("application/json is required")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 32768:
                    raise ValueError("invalid request size")
                def unique(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError("duplicate JSON key")
                        result[key] = value
                    return result
                body = json.loads(self.rfile.read(size), object_pairs_hook=unique)
                result = (app.request if path == "/api/request" else app.decide)(session, body)
                self._send(200, result); return
            if self.command not in {"GET", "HEAD"}:
                self._send(405, {"error": "unsupported method"}); return
            if path == "/api/state" and not parsed.query:
                app.expire_due()
                self._send(200, app.state(session)); return
            if path in {"/", "/requester", "/approver"}:
                if path != "/":
                    app._require(session, (path[1:],))
                self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8"); return
            if path in {"/approval.js", "/approval.css"}:
                name, mime = ("app.js", "text/javascript") if path.endswith(".js") else ("style.css", "text/css")
                self._send(200, (STATIC / name).read_bytes(), mime + "; charset=utf-8"); return
            response = handle_request(self.server.viewer, self.command, "/" if path == "/viewer" else self.path)
            if path == "/viewer" and self.command == "GET" and response.status == 200:
                page = response.body.replace(b"<body>", b'<body><nav><a href="/">Return to refund app / \xe8\xbf\x94\xe5\x9b\x9e\xe5\xae\xa1\xe6\x89\xb9\xe5\xba\x94\xe7\x94\xa8</a></nav>', 1)
                self._send(200, page, "text/html; charset=utf-8"); return
            self._send(response.status, response.body, headers=response.headers)
        except PermissionError as exc:
            status = 401 if not self.headers.get("Authorization") else 403
            self._send(status, {"error": str(exc)}, headers={"WWW-Authenticate": 'Basic realm="RPNH controlled mock clients"'} if status == 401 else None)
        except RegistryConflict as exc:
            self._send(409, {"error": str(exc)})
        except (ValueError, TypeError, KeyError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception:
            # Preserve full original failure in server stderr, without disclosing
            # runtime paths to the client or relabelling an unknown result success.
            import traceback
            traceback.print_exc()
            self._send(500, {"error": "runtime could not confirm completion; inspect the original evidence"})

    do_GET = do_HEAD = do_POST = _handle

    def log_message(self, *args):
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--reopen", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--decision-seconds", type=int, default=900)
    args = parser.parse_args(argv)
    app = ApprovalApp(args.run_dir, workspace=args.workspace, reopen=args.reopen, decision_seconds=args.decision_seconds)
    with ApprovalServer(app, args.port) as server:
        print("Local controlled demo:", server.origin, flush=True)
        print("Open TWO independent browser profiles; use the separate credentials below.", flush=True)
        for user in ("requester", "approver"):
            print(user + " password: " + server.passwords[user], flush=True)
        print("Temporary local access secrets above: keep console/logs private. No real accounts or money.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
