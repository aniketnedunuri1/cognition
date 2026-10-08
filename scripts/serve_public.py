"""Start the MCP HTTP server behind a cloudflared quick tunnel for Devin.

Prints the public MCP URL and Authorization header to paste into
Customize → MCPs → Add custom MCP. Does not write tokens to disk.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
TUNNEL_URL_RE = re.compile(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com")


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    from dotenv import load_dotenv
    load_dotenv(env_path)


def _need_cloudflared() -> str:
    path = shutil.which("cloudflared")
    if path:
        return path
    sys.stderr.write(
        "cloudflared not found. Install it, then re-run this script:\n"
        "  brew install cloudflared\n"
    )
    raise SystemExit(1)


def _ensure_token() -> tuple[str, bool]:
    token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if token:
        return token, False
    token = secrets.token_urlsafe(32)
    os.environ["MCP_AUTH_TOKEN"] = token
    return token, True


def _wait_for_tunnel_url(proc: subprocess.Popen, log_path: Path, timeout: float = 30) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            tail = log_path.read_text(errors="replace")[-2000:]
            raise SystemExit(f"cloudflared exited {proc.returncode} before printing a URL.\n{tail}")
        text = log_path.read_text(errors="replace")
        match = TUNNEL_URL_RE.search(text)
        if match:
            return match.group(0)
        time.sleep(0.2)
    raise SystemExit(
        "Timed out waiting for cloudflared to print a trycloudflare.com URL.\n"
        f"Last output:\n{log_path.read_text(errors='replace')[-2000:]}"
    )


def main() -> int:
    os.chdir(ROOT)
    _load_dotenv()
    cloudflared = _need_cloudflared()
    token, generated = _ensure_token()

    log_path = ROOT / ".cloudflared-devin.log"
    log_f = log_path.open("w")
    cf = subprocess.Popen(
        [cloudflared, "tunnel", "--url", f"http://127.0.0.1:{PORT}", "--no-autoupdate"],
        stdout=log_f, stderr=subprocess.STDOUT, cwd=ROOT,
    )
    server: subprocess.Popen | None = None

    def shutdown(*_args) -> None:
        for proc in (server, cf):
            if proc and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
        log_f.close()

    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    try:
        public = _wait_for_tunnel_url(cf, log_path)
        os.environ["PUBLIC_BASE_URL"] = public
        server = subprocess.Popen(
            [sys.executable, "-m", "server.app", "--http", "--host", "127.0.0.1", "--port", str(PORT)],
            cwd=ROOT, env=os.environ.copy(),
        )
        time.sleep(0.8)
        if server.poll() is not None:
            raise SystemExit(f"MCP server exited {server.returncode} — check MCP_AUTH_TOKEN / PUBLIC_BASE_URL.")

        mcp_url = f"{public.rstrip('/')}/mcp"
        print()
        print("Devin MCP (paste into Customize → MCPs → Add custom MCP)")
        print(f"  Public MCP URL: {mcp_url}")
        print(f"  Header:         Authorization: Bearer {token}")
        print("  Scope:          organization")
        if generated:
            print()
            print("Generated MCP_AUTH_TOKEN for this session (not saved to disk).")
            print("Re-use it next time with:")
            print(f"  export MCP_AUTH_TOKEN={token}")
        print()
        print("Ctrl-C stops the server and the tunnel.")
        print()
        while True:
            if cf.poll() is not None:
                raise SystemExit(f"cloudflared exited {cf.returncode}")
            if server.poll() is not None:
                raise SystemExit(f"MCP server exited {server.returncode}")
            time.sleep(0.5)
    finally:
        shutdown()
        try:
            log_path.unlink(missing_ok=True)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
