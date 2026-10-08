# Account Research Agent

One account, deep. Give it a company domain; get sourced company research, buying centers, intent signals, and Devin-positioned drafts, and it learns from rep feedback. Design: [SPEC.md](SPEC.md).

## Setup

Needs Python ≥ 3.10.

```bash
python3 -m venv .venv            # or: uv venv
.venv/bin/pip install -e '.[dev]'  # or: uv pip install -e '.[dev]'
cp .env.example .env             # fill in ANTHROPIC_API_KEY, EXA_API_KEY, APOLLO_API_KEY
.venv/bin/pytest                 # no API calls
```

> **macOS + conda:** an x86_64 conda Python on Apple Silicon can't install `cryptography` (no matching wheel, so pip falls back to a Rust build).
> Create the venv from a native arm64 Python instead (python.org installer, Homebrew, or `uv venv --python 3.12`).

## Run it

```bash
.venv/bin/python agent.py research acme.com              # brief → briefs/acme.com/<date>.{json,md}, Doc, Slack summary
.venv/bin/python agent.py research acme.com --stage people
.venv/bin/python agent.py feedback acme.com "CTO email too long; lead with the peer metric"
.venv/bin/python agent.py research <demo-domain> --replay # reproduce the committed demo, no data API spend
```

**Auth for the runner:** set `ANTHROPIC_API_KEY` in `.env` (recommended for reviewers). Alternative: if you're logged in to Claude Code (`claude` CLI), the runner uses that login and no key is needed.

The agent gets **only** this MCP server's tools (no built-in web search, no other MCP servers), so every fact has to come through a cited tool call. The CLI prints the Slack summary exactly as the tool rendered it from the validated brief; the agent's own prose is shown only with `-v`, labeled unvalidated.

**Output:** the validated brief is written to `briefs/<domain>/<date>.md` (+ `.json`) and the CLI prints the Slack summary. That Markdown is the canonical record. In Slack, Devin or Claude uses its own Google Docs/Drive connector (the rep signs in once in that product) to copy the brief into a Doc and link it from the summary. The CLI runs with strict MCP config, so it produces Markdown only.

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
| `get_positioning()`, `match_customers(companies)` | `knowledge/` (built from cognition.com + devin.ai) |
| `refresh_positioning()`, `apply_knowledge_update(...)` | Incremental knowledge refresh with source rules enforced in code |
| `lint_message(...)`, `save_brief(...)`, `get_last_brief(...)`, `set_brief_link(...)` | Draft checks, validated briefs + rendered Markdown, since-last-run diff, Doc link from the harness's Google connector |
| `get_playbook()`, `add_playbook_rule(...)`, `add_golden_example(...)` | Feedback memory in `playbook/` (git-committed on change) |

Every result carries a `source_id` per document. The model cites `source_id`s only; the validator (`server/schema.py`) drops anything that doesn't resolve or whose quote doesn't verify, and URLs are filled in from the cache at render time.
