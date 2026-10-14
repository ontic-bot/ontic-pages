"""Exercise deployment environment writing using a fake box rooted in tmp_path."""

import os
import shutil
import subprocess
from pathlib import Path

from ontic_pages.config import load_config


def test_install_propagates_enable_and_rollback(tmp_path):
    source = Path(__file__).resolve().parents[1] / "deploy"
    deploy = tmp_path / "deploy"
    shutil.copytree(source, deploy, ignore=shutil.ignore_patterns("secrets.env"))
    etc = tmp_path / "etc"
    (etc / "containers/systemd").mkdir(parents=True)
    (etc / "caddy/conf.d").mkdir(parents=True)
    (etc / "caddy/Caddyfile").write_text(
        "import conf.d/*\non_demand_tls {\n ask http://127.0.0.1:8790/_tls-ask\n}\n"
    )
    script = deploy / "pages.sh"
    # Redirect fixed machine paths only. Run the real shell functions against harmless commands.
    script.write_text(script.read_text().replace("/etc/", str(etc) + "/"))
    commands = tmp_path / "commands"
    binary = tmp_path / "bin"
    binary.mkdir()
    stubs = {
        "id": "echo 0",
        "git": "echo review-test",
        "chown": "exit 0",
        "podman": 'echo "podman $*" >> "$TEST_COMMANDS"',
        "systemctl": 'echo "systemctl $*" >> "$TEST_COMMANDS"',
        "caddy": 'echo "caddy $*" >> "$TEST_COMMANDS"',
        "install": 'src=; dst=; for arg do src=$dst; dst=$arg; done; cp "$src" "$dst"',
    }
    for name, body in stubs.items():
        path = binary / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    env = {**os.environ, "PATH": f"{binary}:{os.environ['PATH']}", "TEST_COMMANDS": str(commands)}
    secrets = deploy / "secrets.env"
    required = (
        "GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET OAUTH2_COOKIE_SECRET OAUTH2_EMAIL_DOMAIN "
        "ONTIC_PAGES_BUCKET ONTIC_PAGES_ENDPOINT ONTIC_PAGES_REGION "
        "AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY"
    ).split()
    for setting, enabled in [("1", True), ("0", False), ("", True)]:
        secrets.write_text(
            "".join(f"{key}=test\n" for key in required)
            + "ONTIC_PAGES_TOKEN_SECRET="
            + "x" * 32
            + "\n"
            + f"ONTIC_PAGES_DIRECT_FILES={setting}\n"
        )
        secrets.chmod(0o600)
        subprocess.run(["sh", str(script), "install"], env=env, check=True, capture_output=True)
        gateway_env = dict(
            line.split("=", 1)
            for line in (etc / "ontic-pages/gateway.env").read_text().splitlines()
        )
        assert load_config(tmp_path / "no-config", env=gateway_env).is_direct_files is enabled
        assert gateway_env["ONTIC_PAGES_TOKEN_SECRET"] == "x" * 32
        assert "GOOGLE_CLIENT_SECRET" not in gateway_env
        assert (etc / "ontic-pages/gateway.env").stat().st_mode & 0o777 == 0o600
    log = commands.read_text()
    assert log.count("podman build") == 3
    assert (
        log.count(
            "systemctl restart pages-network.service pages-gateway.service "
            "pages-oauth2-proxy.service"
        )
        == 3
    )
    assert log.count("systemctl reload caddy") == 3
