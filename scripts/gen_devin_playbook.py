#!/usr/bin/env python3
"""Build Devin Playbook text (macro !account-research) from playbook/playbook.md.

Prints to stdout. Optionally writes a generated file (gitignored) with --out.
Do not paste into Devin until playbook changes on main have merged.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAYBOOK = ROOT / "playbook" / "playbook.md"
DEFAULT_OUT = ROOT / "playbook" / "devin_playbook.generated.md"

HEADER = """# Account Research — Devin Playbook

Macro: `!account-research`

## Slack

```
@Devin !account-research acme.com
```

Optional stage (company / people / signals / draft / feedback):

```
@Devin !account-research acme.com people
```

The session uses the HTTP MCP server (not stdio). Learned rules and briefs live
on that server, so they persist across Slack sessions.

## MCP (do this once)

1. Customize → MCPs → Add custom MCP
2. URL: the public Streamable HTTP URL from `scripts/devin-http.sh`
   (typically `https://<tunnel>.trycloudflare.com/mcp`)
3. Header: `Authorization: Bearer <MCP_AUTH_TOKEN>`
4. Scope: **organization** (Slack-triggered sessions need org scope)

Start the tunnel + server:

```
./scripts/devin-http.sh
```

Regenerate this Playbook text after `playbook/playbook.md` changes:

```
uv run python scripts/gen_devin_playbook.py
```

---

"""


def generate(playbook_path: Path = PLAYBOOK) -> str:
    body = playbook_path.read_text()
    if not body.endswith("\n"):
        body += "\n"
    return HEADER + body


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate Devin Playbook text from playbook/playbook.md")
    ap.add_argument("--out", nargs="?", const=str(DEFAULT_OUT), default=None,
                    help="write the generated text (default path if flag is passed with no value)")
    args = ap.parse_args()
    text = generate()
    sys.stdout.write(text)
    if args.out:
        out = Path(args.out)
        out.write_text(text)
        sys.stderr.write(f"Wrote {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
