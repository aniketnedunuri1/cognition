# Account Research Agent

One account, deep. Give it a company domain; get sourced company research, buying centers, intent signals, and Devin-positioned drafts, and it learns from rep feedback. Design: [SPEC.md](SPEC.md).

## Setup

Needs Python ≥ 3.10.

```bash
python3 -m venv .venv            # or: uv venv
.venv/bin/pip install -e '.[dev]'  # or: uv pip install -e '.[dev]'
cp .env.example .env             # fill in EXA_API_KEY, APOLLO_API_KEY
.venv/bin/pytest                 # no API calls
```

> **macOS + conda:** an x86_64 conda Python on Apple Silicon can't install `cryptography` (no matching wheel, so pip falls back to a Rust build).
> Create the venv from a native arm64 Python instead (python.org installer, Homebrew, or `uv venv --python 3.12`).

## MCP server

```bash
.venv/bin/python -m server.app              # stdio (local runner, Claude Code)
.venv/bin/python -m server.app --http       # Streamable HTTP on :8765/mcp, needs MCP_AUTH_TOKEN
.venv/bin/python -m server.app --replay     # cache only: reproduces the committed demo with no API spend
```

| Tool | Source |
|---|---|
| `get_company(domain)` | Apollo org enrichment (Free plan) |
| `get_jobs(domain)` | Apollo job postings, deduped by title |
| `search_web(query, since?)` | Exa search |
| `fetch_page(url_or_source_id)` | Exa contents |
| `search_people(company, roles)` | Exa people search |
| `verify_quote(source_id, quote)` | Cached page text |

Every result carries a `source_id` per document. The model cites `source_id`s only; the validator (`server/schema.py`) drops anything that doesn't resolve or whose quote doesn't verify, and URLs are filled in from the cache at render time.
