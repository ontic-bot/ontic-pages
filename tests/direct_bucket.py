"""A private bucket stand-in for direct-file tests, including browser requests.

Grants bind a method and path to an expiry. This models presigned URLs without real credentials;
it is not an implementation of S3 signing. Only configured origins receive CORS headers.
"""

import http.client
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

from helpers import stop


def fetch(url, headers=None, method="GET"):
    u = urlsplit(url)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
    try:
        conn.request(method, f"{u.path}?{u.query}", headers=headers or {})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


class DirectBucket:
    def __init__(self, store, monkeypatch):
        self.store = store
        self.now = 0.0
        self.grants = {}
        self.requests = []
        self.origins = set()
        self.allowed_headers = {"*"}
        bucket = self

        class Handler(BaseHTTPRequestHandler):
            def cors(self):
                origin = self.headers.get("Origin")
                if origin in bucket.origins:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                    self.send_header("Access-Control-Expose-Headers", "content-range, etag")

            def do_OPTIONS(self):
                allowed = (
                    self.headers.get("Origin") in bucket.origins
                    and self.headers.get("Access-Control-Request-Method") in ("GET", "HEAD")
                    and (
                        "*" in bucket.allowed_headers
                        or set(
                            h.strip().lower()
                            for h in self.headers.get("Access-Control-Request-Headers", "").split(
                                ","
                            )
                            if h.strip()
                        )
                        <= bucket.allowed_headers
                    )
                )
                self.send_response(204 if allowed else 403)
                if allowed:
                    self.cors()
                    self.send_header("Access-Control-Allow-Methods", "GET, HEAD")
                    self.send_header(
                        "Access-Control-Allow-Headers", ", ".join(bucket.allowed_headers)
                    )
                self.end_headers()

            def do_GET(self):
                u = urlsplit(self.path)
                grant = bucket.grants.get(parse_qs(u.query).get("signature", [""])[0])
                valid = grant and grant[:2] == (self.command, u.path) and bucket.now < grant[2]
                _, _, key = unquote(u.path).lstrip("/").partition("/")
                found = bucket.store.client.objects.get((bucket.store.bucket, key))
                code = 403 if not valid else 200 if found else 404
                data, ctype = found if code == 200 else (b"", "text/plain")
                length = len(data)
                content_range = None
                if code == 200 and (ranged := self.headers.get("Range")):
                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", ranged)
                    if match and any(match.groups()):
                        a, b = match.groups()
                        start = int(a) if a else max(0, length - int(b))
                        end = min(int(b), length - 1) if a and b else length - 1
                        if start <= end and start < length:
                            code = 206
                            data = data[start : end + 1]
                            content_range = f"bytes {start}-{end}/{length}"
                        else:
                            code = 416
                    else:
                        code = 416
                    if code == 416:
                        data, content_range = b"", f"bytes */{length}"
                bucket.requests.append((self.command, u.path, code, self.headers.get("Range")))
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, no-cache")
                self.send_header("Accept-Ranges", "bytes")
                if content_range:
                    self.send_header("Content-Range", content_range)
                self.cors()
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)

            do_HEAD = do_GET

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        monkeypatch.setattr(store, "presign_get", self.presign_get)

    def presign_get(self, key, seconds=3600):
        self.store.client.calls.append("generate_presigned_url")
        signature = secrets.token_hex(16)
        path = f"/{self.store.bucket}/{quote(key)}"
        self.grants[signature] = ("GET", path, self.now + seconds)
        return f"{self.origin}{path}?signature={signature}&expires={seconds}"

    def close(self):
        stop(self.server)
