"""Start a gateway on a free port and talk to it as a browser would, by Host header."""

from __future__ import annotations

import http.client
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from ontic_pages.gateway import Site, make_server

APEX = "pages.test"
ORIGIN = f"https://{APEX}"
SITE = Site(ORIGIN, email_domain="onticlabs.io")
TEAM = "tester@onticlabs.io"


def cookie_identity(cookie: str) -> tuple[str, list[str]]:
    """Stands in for oauth2-proxy: the cookie `who=<email>` is signed in as <email>."""
    for part in cookie.split(";"):
        key, _, value = part.strip().partition("=")
        if key == "who":
            return value, []
    return "", []


def start(store, **kwargs):
    # General transport tests exercise server delivery; direct-file tests enable redirects.
    kwargs.setdefault("direct_files", False)
    kwargs.setdefault("site", SITE)
    kwargs.setdefault("identity", cookie_identity)
    kwargs.setdefault("ttl", 0)
    srv = make_server(store, "127.0.0.1", 0, **kwargs)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def stop(srv) -> None:
    srv.shutdown()
    srv.server_close()


def request(srv, path, host=APEX, who=TEAM, headers=None, method="GET", body=None):
    """(status, headers, body). `who` signs in by cookie (None: signed out)."""
    sent = {"Host": host, **({"Cookie": f"who={who}"} if who else {}), **(headers or {})}
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
    conn.request(method, path, body=body, headers=sent)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, dict(resp.getheaders()), data


def content(srv, name, path, **kwargs):
    """A request to the page's own host, <name>.pages.test."""
    return request(srv, path, host=f"{name}.{APEX}", **kwargs)


FRAME = {"Sec-Fetch-Dest": "iframe", "Sec-Fetch-Site": "same-site"}
DOCUMENT = {"Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "none"}


SECRET = "test-secret-" + "x" * 32


def start_local(store, **kwargs):
    """A gateway whose apex is its own address, http://127.0.0.1:<port>, so the command line
    can talk to it with ONTIC_PAGES_URL. With a token secret."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    kwargs.setdefault("direct_files", False)
    kwargs.setdefault("site", Site(f"http://127.0.0.1:{port}", email_domain="onticlabs.io"))
    kwargs.setdefault("identity", cookie_identity)
    kwargs.setdefault("ttl", 0)
    kwargs.setdefault("token_secret", SECRET)
    srv = make_server(store, "127.0.0.1", port, **kwargs)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.url = f"http://127.0.0.1:{port}"
    return srv


def upload_server(s3):
    """Stands in for the bucket's presigned PUT and GET: stores into the fake S3 at the URLs its
    generate_presigned_url makes, and refuses a Content-Type other than the signed one (403),
    as S3 does; GET answers a stored object. `fail` (a list) makes the next requests answer
    500."""

    class Put(BaseHTTPRequestHandler):
        def do_PUT(self):
            url = urlsplit(self.path)
            bucket, _, key = unquote(url.path)[1:].partition("/")
            signed = parse_qs(url.query)["ct"][0]
            data = self.rfile.read(int(self.headers["Content-Length"]))
            srv.puts.append((key, self.headers.get("Content-Type")))
            if srv.fail:
                srv.fail.pop()
                code = 500
            elif self.headers.get("Content-Type") != signed:
                code = 403
            else:
                s3.objects[(bucket, key)] = (data, signed)
                code = 200
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            url = urlsplit(self.path)
            bucket, _, key = unquote(url.path)[1:].partition("/")
            srv.gets.append(key)
            found = s3.objects.get((bucket, key)) if "get" in parse_qs(url.query) else None
            code = 200 if found else 404
            if srv.fail:
                srv.fail.pop()
                code, found = 500, None
            data = found[0] if found else b""
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Put)
    srv.puts, srv.gets, srv.fail = [], [], []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    s3.upload_base = f"http://127.0.0.1:{srv.server_port}"
    return srv
