# Account Research Agent

One account, deep. Give it a company domain; get sourced company research, buying centers, intent signals, and Devin-positioned drafts, and it learns from rep feedback. Design: [SPEC.md](SPEC.md).

## Setup

Needs [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you).

```bash
uv sync                    # creates .venv with Python 3.12 + deps (incl. pytest)
cp .env.example .env       # fill in ANTHROPIC_API_KEY, EXA_API_KEY, APOLLO_API_KEY
uv run pytest              # no API calls
```

## Run it

```bash
uv run python agent.py research acme.com              # brief → briefs/acme.com/<date>.{json,md,docx} + Slack summary
uv run python agent.py research acme.com --stage people
uv run python agent.py feedback acme.com "CTO email too long; lead with the peer metric"
uv run python agent.py research <demo-domain> --replay # reproduce the committed demo, no data API spend
```

**Auth for the runner:** set `ANTHROPIC_API_KEY` in `.env` (recommended for reviewers). Alternative: if you're logged in to Claude Code (`claude` CLI), the runner uses that login and no key is needed.

The agent gets **only** this MCP server's tools (no built-in web search, no other MCP servers), so every fact has to come through a cited tool call. The CLI prints the Slack summary exactly as the tool rendered it from the validated brief; the agent's own prose is shown only with `-v`, labeled unvalidated.

**Output:** the validated brief is written to `briefs/<domain>/<date>.json` (source of truth) and rendered in code to `.md` and `.docx`; the model never touches the rendered content. The CLI prints the Slack summary with the local `.docx` path. In HTTP mode (Devin), "Full brief" is a download link served by the same server at an unguessable per-brief path.

## MCP server

```bash
uv run python -m server.app              # stdio (local runner, Claude Code)
uv run python -m server.app --http       # Streamable HTTP on :8765/mcp; needs MCP_AUTH_TOKEN + PUBLIC_BASE_URL
uv run python -m server.app --replay     # cache only: reproduces the committed demo with no API spend
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
| `lint_message(...)`, `save_brief(...)`, `get_last_brief(...)` | Draft checks, validated briefs rendered to Markdown + .docx, since-last-run diff |
| `get_playbook()`, `add_playbook_rule(...)`, `add_golden_example(...)` | Feedback memory in `playbook/` (git-committed on change) |

Every result carries a `source_id` per document. The model cites `source_id`s only; the validator (`server/schema.py`) drops anything that doesn't resolve or whose quote doesn't verify, and URLs are filled in from the cache at render time.

HTTP mode also serves `GET /briefs/<token>/<file>.docx` (no bearer token: the random per-brief token is the access control); the MCP endpoint itself requires `MCP_AUTH_TOKEN`.
