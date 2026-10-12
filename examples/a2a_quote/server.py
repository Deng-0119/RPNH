"""One controlled local holder frontdoor and the stock original-run Viewer."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit

from cpn.frontend.dashboard import RegistryDashboard
from cpn.frontend.server import handle_request
from cpn.rpnh.registry.event_store import RegistryConflict
from .app import A2AQuoteApp
from .host import registration
from .native import catalog

STATIC = Path(__file__).with_name("static")


class A2AQuoteServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, app, port=0):
        self.app = app
        # All browsers on this controlled loopback endpoint share the one holder
        # session. The verifier session is never exposed or chosen by HTTP data.
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = "http://127.0.0.1:" + str(self.server_port)
        inventory = catalog()
        registration().bind_schema_catalog(inventory)
        self.viewer = RegistryDashboard(app.directory / "run", catalog=inventory)


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
            parsed = urlsplit(self.path)
            path, app = parsed.path, self.server.app
            session = app.sessions["holder"]
            if self.command == "POST":
                if path not in {"/api/select", "/api/renew"} or parsed.query:
                    self._send(404, {"error": "unknown action"}); return
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("application/json is required")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 32768:
                    raise ValueError("invalid request size")
                def unique(pairs):
                    value = {}
                    for key, item in pairs:
                        if key in value:
                            raise ValueError("duplicate JSON key")
                        value[key] = item
                    return value
                body = json.loads(self.rfile.read(size), object_pairs_hook=unique)
                self._send(200, (app.select if path == "/api/select" else app.renew)(session, body)); return
            if self.command not in {"GET", "HEAD"}:
                self._send(405, {"error": "unsupported method"}); return
            if path == "/api/state" and not parsed.query:
                app.expire_due()
                self._send(200, app.state(session)); return
            if path in {"/", "/holder"}:
                self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8"); return
            if path in {"/a2a.js", "/a2a.css"}:
                name, mime = ("app.js", "text/javascript") if path.endswith(".js") else ("style.css", "text/css")
                self._send(200, (STATIC / name).read_bytes(), mime + "; charset=utf-8"); return
            response = handle_request(self.server.viewer, self.command, "/" if path == "/viewer" else self.path)
            if path == "/viewer" and self.command == "GET" and response.status == 200:
                page = response.body.replace(b"<body>", '<body><nav><a href="/">Return to quote app / 返回报价应用</a></nav>'.encode(), 1)
                self._send(200, page, "text/html; charset=utf-8"); return
            self._send(response.status, response.body, headers=response.headers)
        except PermissionError as exc:
            self._send(403, {"error": str(exc)})
        except RegistryConflict as exc:
            self._send(409, {"error": str(exc)})
        except (ValueError, TypeError, KeyError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception:
            import traceback
            traceback.print_exc()
            self._send(500, {"error": "runtime outcome is unconfirmed; inspect original evidence"})

    do_GET = do_HEAD = do_POST = _handle

    def log_message(self, *args):
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True, help="app directory; native Registry is its run/ child")
    parser.add_argument("--reopen", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--proof-seconds", type=int, default=900)
    args = parser.parse_args(argv)
    app = A2AQuoteApp(args.run_dir, workspace=args.workspace, reopen=args.reopen, proof_seconds=args.proof_seconds)
    with A2AQuoteServer(app, args.port) as server:
        print("Controlled local synthetic holder:", server.origin, flush=True)
        print("Stage 1 fixture association only; Stage 2 real A2A/Credo/OID4VP BLOCKED_NOT_RUN.", flush=True)
        print("All local browsers share one controlled holder; no external login or credential generation.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
