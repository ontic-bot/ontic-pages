"""Optional real Caddy check: CADDY_PATH selects the binary used in deployment."""

import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest
from direct_bucket import fetch

CADDY = os.environ.get("CADDY_PATH") or shutil.which("caddy")
pytestmark = pytest.mark.skipif(not CADDY, reason="needs Caddy (or CADDY_PATH)")


@pytest.mark.parametrize("site", [0, 1])
def test_redirect_location_is_sent_but_not_logged(tmp_path, site):
    source = Path(__file__).resolve().parents[1] / "deploy/pages.caddy"
    blocks = re.findall(r"\tlog \{.*?\n\t\}", source.read_text(), re.S)
    assert len(blocks) == 2
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    target = "https://bucket.example/file?X-Amz-Signature=secret-test-grant"
    config = tmp_path / "Caddyfile"
    config.write_text(
        "{\n admin off\n auto_https off\n}\n"
        f"http://127.0.0.1:{port} {{\n header Location {target}\n respond 302\n"
        + blocks[site]
        + "\n}\n"
    )
    output = tmp_path / "caddy.log"
    with output.open("w") as log:
        proc = subprocess.Popen(
            [CADDY, "run", "--config", str(config), "--adapter", "caddyfile"],
            stdout=log,
            stderr=log,
        )
        try:
            for _ in range(100):
                try:
                    status, headers, _ = fetch(f"http://127.0.0.1:{port}/file")
                    break
                except OSError:
                    assert proc.poll() is None, output.read_text()
                    time.sleep(0.05)
            else:
                pytest.fail("Caddy did not start")
            assert status == 302 and headers["Location"] == target
        finally:
            proc.terminate()
            proc.wait(timeout=5)
    records = [json.loads(line) for line in output.read_text().splitlines() if line.startswith("{")]
    access = [r for r in records if r.get("logger", "").startswith("http.log.access")]
    assert access and access[-1]["status"] == 302
    assert "Location" not in access[-1]["resp_headers"]
    assert "secret-test-grant" not in output.read_text()
