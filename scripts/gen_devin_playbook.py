#!/usr/bin/env python3
"""Build Devin Playbook texts from playbook/*.md (shared.md is prepended to each).

Prints all three to stdout, separated by headers. With --out, writes one
gitignored file per playbook into playbook/ (devin_<name>.generated.md).
Do not paste into Devin until playbook changes on main have merged.
"""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAYBOOK_DIR = ROOT / "playbook"

MCP_SETUP = """## MCP (do this once)

1. Customize → MCPs → Add custom MCP
2. URL: the public Streamable HTTP URL from `scripts/devin-http.sh`
   (typically `https://<tunnel>.trycloudflare.com/mcp`)
3. Header: `Authorization: Bearer <MCP_AUTH_TOKEN>`
4. Scope: **organization** (Slack-triggered sessions need org scope)

Start the tunnel + server:

```
./scripts/devin-http.sh
```

Regenerate this Playbook text after `playbook/` changes:

```
uv run python scripts/gen_devin_playbook.py
```

---

"""

PLAYBOOKS = [
    ("research", "!account-research", """## Slack

```
@Devin !account-research acme.com
```

Optional stage (company / people / signals / draft):

```
@Devin !account-research acme.com people
```

The session uses the HTTP MCP server (not stdio). Learned rules and briefs live
on that server, so they persist across Slack sessions.

"""),
    ("feedback", "!feedback", """## Slack

```
@Devin !feedback acme.com "rep feedback text"
```

"""),
    ("positioning", "!refresh-positioning", """## Slack / schedule

```
@Devin !refresh-positioning
```

Runs on a schedule; keeps customers/use_cases/proof quotes current.

"""),
]


def generate(name: str) -> str:
    shared = (PLAYBOOK_DIR / "shared.md").read_text()
    body = (PLAYBOOK_DIR / f"{name}.md").read_text()
    macro, header = next((m, h) for n, m, h in PLAYBOOKS if n == name)
    title = body.splitlines()[0].lstrip("# ")
    return f"# {title} — Devin Playbook\n\nMacro: `{macro}`\n\n" + header + MCP_SETUP \
        + shared + "\n---\n\n" + body


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate Devin Playbook texts from playbook/")
    ap.add_argument("--out", action="store_true",
                    help="write gitignored copies to playbook/devin_<name>.generated.md")
    args = ap.parse_args()
    for name, macro, _ in PLAYBOOKS:
        text = generate(name)
        print(f"{'=' * 60}\n{macro}  →  playbook/devin_{name}.generated.md\n{'=' * 60}\n")
        print(text)
        if args.out:
            out = PLAYBOOK_DIR / f"devin_{name}.generated.md"
            out.write_text(text)
            print(f"Wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
