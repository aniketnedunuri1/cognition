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
uv run python agent.py research acme.com              # brief → briefs/acme.com/<date>.{json,md,docx,html} + Slack summary
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
| `search_web(query, since?, include_domains?)` | Exa search (`include_domains=["linkedin.com"]` for posts/talks) |
| `fetch_page(url_or_source_id)` | Exa contents |
| `search_people(company, roles)` | Exa people search |
| `search_company_linkedin(company, domain?, linkedin_slug?, since?)` | Exa search on `linkedin.com/posts|company/<slug>` path prefixes: indexed company posts, best-effort tags/reposts from snippets (no scraping, no feed guarantee) |
| `get_org_chart(domain, people?)` | Sumble inferred org chart: sourced people → inferred manager + direct reports (labeled inference; ~50–100 credits/account) |
| `get_tech_stack(domain)` | Sumble confirmed-used tech stack verified via job posts, grouped by business function — preferred over Apollo's website-tag hint |
| `verify_quotes(quotes)` | Cached page text — batch, one call for all quotes |
| `get_positioning()`, `match_customers(companies)` | `knowledge/` (built from cognition.com + devin.ai) |
| `refresh_positioning()`, `apply_knowledge_update(...)` | Incremental knowledge refresh with source rules enforced in code |
| `lint_message(...)`, `save_brief(...)`, `get_last_brief(...)` | Draft checks, validated briefs rendered to Markdown + .docx + annotatable HTML, since-last-run diff |
| `get_playbook()`, `add_playbook_rule(...)`, `add_golden_example(...)` | Feedback memory in `playbook/` (git-committed on change) |
| `get_brief_feedback(domain)`, `mark_brief_feedback_processed(...)` | Rep comments submitted on the HTML report → distill into rules |

Every result carries a `source_id` per document. The model cites `source_id`s only; the validator (`server/schema.py`) drops anything that doesn't resolve or whose quote doesn't verify, and URLs are filled in from the cache at render time.

HTTP mode also serves `GET /briefs/<token>/<file>.docx` and `GET /report/<token>` (no bearer token: the random per-brief token is the access control); the MCP endpoint itself requires `MCP_AUTH_TOKEN`. The report page lets a rep select text, leave comments, and submit them in one batch to `POST /report/<token>/comments`; they land in `briefs/<domain>/<date>.comments.json` for the next run to distill into rules.

## Devin setup

Devin talks to this server over Streamable HTTP (not stdio). A stdio server would run on Devin's ephemeral VM and lose briefs and learned rules when the session ends.

### 1. Start the public MCP URL

```bash
./scripts/devin-http.sh
```

This starts `python -m server.app --http` on `:8765` and a cloudflared tunnel in front of it. If `cloudflared` is missing: `brew install cloudflared`. If `MCP_AUTH_TOKEN` is unset, the script generates one for the session and prints it (it is not written to disk).

Copy from the script output:

- **Public MCP URL** — `https://<tunnel>.trycloudflare.com/mcp`
- **Header** — `Authorization: Bearer <MCP_AUTH_TOKEN>`

`PUBLIC_BASE_URL` is the tunnel origin (no `/mcp`). Its hostname is added to FastMCP `allowed_hosts` / `allowed_origins` so Devin's requests are not rejected.

### 2. Add the custom MCP in Devin

1. Customize → MCPs → Add custom MCP
2. URL: the public MCP URL above
3. Header: `Authorization: Bearer <MCP_AUTH_TOKEN>`
4. Scope: **organization** (Slack-triggered sessions need org scope)

### 3. Create the Playbooks

The playbook is split three ways so each session loads only what it needs;
`playbook/shared.md` is prepended to each generated text:

| File | Devin Playbook macro | Purpose |
|---|---|---|
| `playbook/research.md` | `!account-research` | Stages 0–4, one account or one `--stage` |
| `playbook/feedback.md` | `!feedback` | Rep feedback → learned rules / golden examples |
| `playbook/positioning.md` | `!refresh-positioning` | Scheduled knowledge refresh |

Generate all three texts (do not hand-edit a duplicate):

```bash
uv run python scripts/gen_devin_playbook.py
# or write gitignored copies:
uv run python scripts/gen_devin_playbook.py --out
```

In Devin, create one Playbook per macro and paste each generated text **after** playbook changes on this branch have landed (regenerate if `playbook/` changed).

Slack:

```
@Devin !account-research acme.com
```
