"""HTTP mode: bearer auth, Host/Origin allowlist, brief download stays token-free."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from server.app import build_server, public_host_origin

ROOT = Path(__file__).resolve().parent.parent
AUTH = {"Authorization": "Bearer secret"}
PUBLIC = "https://abc.trycloudflare.com"


def test_public_host_origin_parses_tunnel_url():
    hosts, origins = public_host_origin(PUBLIC)
    assert hosts == ["abc.trycloudflare.com"]
    assert origins == [PUBLIC]


def test_public_host_origin_rejects_empty_host():
    with pytest.raises(ValueError, match="no hostname"):
        public_host_origin("not-a-url")


def test_mcp_401_without_token():
    with TestClient(build_server("secret").http_app()) as client:
        assert client.post("/mcp", json={}).status_code == 401
        assert client.post("/mcp", json={}, headers=AUTH).status_code != 401


def test_tunnel_host_allowed_foreign_host_rejected():
    mcp = build_server("secret", public_base_url=PUBLIC)
    with TestClient(mcp.http_app()) as client:
        allowed = client.post(
            "/mcp", json={},
            headers={**AUTH, "Host": "abc.trycloudflare.com"},
        )
        assert allowed.status_code != 421
        rejected = client.post(
            "/mcp", json={},
            headers={**AUTH, "Host": "evil.example"},
        )
        assert rejected.status_code == 421


def test_brief_download_stays_token_free_on_tunnel_host():
    mcp = build_server("secret", public_base_url=PUBLIC)
    with TestClient(mcp.http_app()) as client:
        r = client.get(
            "/briefs/not-a-token/brex.com.docx",
            headers={"Host": "abc.trycloudflare.com"},
        )
        assert r.status_code == 404  # unguessable path; no bearer required
        assert r.status_code != 401


def test_stdio_build_does_not_enable_host_guard():
    with TestClient(build_server("secret").http_app()) as client:
        r = client.post("/mcp", json={}, headers={**AUTH, "Host": "evil.example"})
        assert r.status_code != 421


def test_gen_devin_playbook_includes_macro_and_mcp_steps():
    script = ROOT / "scripts" / "gen_devin_playbook.py"
    out = subprocess.check_output([sys.executable, str(script)], cwd=ROOT, text=True)
    assert "!account-research" in out
    assert "@Devin !account-research acme.com" in out
    assert "Add custom MCP" in out
    assert "Authorization: Bearer" in out
    assert "organization" in out
    assert (ROOT / "playbook" / "playbook.md").read_text() in out
