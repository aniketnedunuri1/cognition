#!/usr/bin/env bash
# Start the MCP server + a cloudflared tunnel and print the Devin paste-in URL + header.
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared not found. Install it, then re-run this script:" >&2
  echo "  brew install cloudflared" >&2
  exit 1
fi
exec uv run python scripts/serve_public.py "$@"
