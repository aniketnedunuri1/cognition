# Account Research Agent — Spec

One account, deep. A rep gives a company name or domain; the agent returns everything needed to break in: who they are, who to talk to, evidence they could use Devin today, and messages worth sending. Then it learns from the rep's feedback.

Every claim carries a source. "Not found" and "Not now" are valid outputs.

---

## Core principle: no made-up data

A wrong fact costs a rep more than a missing one. It burns credibility with the prospect and wastes hours. So:

1. **Every fact has a source from this run.** A URL or API response fetched by a tool during this run. No source → the field says **"Not found."**
2. **The model's own knowledge is never a source.** It may "know" a CTO's name from training data that is years old. Facts come from tool calls only.
3. **No fallbacks, defaults, or placeholders.** If the data isn't there, the section is empty or says "Not found." Never fill a gap with a plausible guess.
4. **Report what a source says, attributed.** "Apollo reports $700M," not "$700M." Third-party numbers are never restated as our own estimate.
5. **Conflicts are recorded, not silently resolved.** The JSON keeps both facts. The human brief shows one working number (Apollo if present, else the first) and puts material disagreements (>25%) in a footnote.
6. **Judgment is separated from fact.** Verdict, "likely champion," recommended entry point, and ABM themes are labeled as the agent's assessment and cite the facts they rest on. Nothing inferred is presented as fact (e.g. no asserted reporting lines).
7. **Enforced in code, not just the prompt.** Every tool result carries a `source_id` per document; the model cites `source_id`s only, never URLs. The validator resolves each against the cache and drops (and counts) anything that doesn't resolve; `verify_quotes` checks every quote against the cached full text of the cited source; people must be named in a source they cite. Real URLs + retrieved dates are filled in from the cache at render time.

---

## User loop

1. Rep: `@Devin research acme.com` in Slack (or `python agent.py research acme.com` locally).
2. Agent runs company → people → signals and saves the validated brief; the server renders it to Markdown and .docx in code.
3. Agent replies in thread with the tool-rendered summary + the brief's .docx download link.
4. Rep picks contacts (or accepts the suggested top 3).
5. Agent drafts email + LinkedIn per contact, posts in thread.
6. Rep edits / comments / approves.
7. Agent distills feedback into playbook rules, confirms what it learned.
**Never auto-send.** No Gmail or other sending integration.

Each stage can also run on its own (`company`, `people`, `signals`, `draft`).

---

## Architecture

```
playbook/        the brain: stage instructions, signal bar, messaging rules, learned rules
knowledge/       Devin positioning, use cases, customers (incrementally refreshed)
server/          MCP server: deterministic tools (data + state, no judgment)
agent.py         Claude Agent SDK runner: playbook as system prompt + MCP server
cache/           every fetched source, keyed by URL (reproducibility + quote verification)
briefs/          dated JSON + markdown per account (enables "since last run")
```

- **Same brain and tools everywhere.** The CLI runner is for reviewers / local use; Devin (Playbook + MCP) in Slack is the deployment story.
- **Data vs. judgment split.** Bad signal → fix playbook. Missing data → fix a tool.
- **Python + FastMCP.**

### Server modes & state

The server owns all state (cache, briefs, learned rules, golden examples, knowledge). It must run in **one place** so state persists across sessions.

| Mode | Used by | Notes |
|---|---|---|
| stdio | CLI runner, Claude Code | Local process, state on local disk |
| **HTTP (Streamable)** | Devin | Required: a stdio server would run on Devin's ephemeral VM and lose learned rules/briefs when the session ends |

- **Build / Loom:** server runs locally in HTTP mode, exposed via tunnel (cloudflared / ngrok). CLI and Devin share the same server and state — feedback given in Slack shows up in the next CLI run.
- **Production:** deploy to a small host (Fly.io / Render) with a persistent volume. Bearer-token auth header; keys in Devin Secrets.

### Devin setup

- Custom MCP (HTTP) added under Customize → MCPs → Add custom MCP (bearer header, org scope).
- Workflow lives in a Devin Playbook with macro `!account-research`. Slack: `@Devin !account-research acme.com`.
- Playbook text is generated from `playbook/` so the CLI and Devin never drift.
- Same generation step emits a Claude Skill (`SKILL.md`); with the HTTP server as a custom connector, `@Claude` in Slack can run it too (unverified: test before promising).

### MCP tools

| Tool | Purpose |
|---|---|
| `search_web(query, since?, include_domains?)` | Exa `/search`, cached. Covers blogs, talks, news, earnings transcripts, filings, GitHub, Devin mentions. `include_domains=["linkedin.com"]` for posts/talks/interviews |
| `fetch_page(url_or_source_id, offset?)` | Exa `/contents`, cached. Full text cached; model gets a 6k-char excerpt (page with `offset`). Job descriptions and any known URL |
| `search_people(company, roles)` | Exa `/search` with `category: "people"`: current title, history, start dates, team descriptions |
| `search_company_linkedin(company, domain?, linkedin_slug?, since?)` | Exa `/search` against `linkedin.com/posts/<slug>` + `linkedin.com/company/<slug>` path prefixes (slug from the arg or the cached Apollo org's linkedin_url; never invented). Three cached searches: posts, mentions, reposts. Rows carry `kind` (original / possible_repost / unknown, from URL + snippet language only) and `possible_tags` (names / `/in/` links literally in the snippet). Not a feed: only what Exa indexed; per-query `not_fetched` on failure |
| `verify_quotes(quotes)` | Deterministic check that each quote exists in its source's cached full text — batched, one call per set (a quote cited to the wrong source fails) |
| `get_company(domain)` | Apollo org enrichment (Free plan): firmographics, parent org, revenue, dept headcount, 6/12/24mo growth, funding, AI tools, engineering tech stack (eng categories only; website tags — a hint/cross-check, never a stated fact without a job post / blog / profile) |
| `get_org_chart(domain, people?)` | Sumble inferred org chart: anchors sourced people (LinkedIn/name match) to Sumble leaders, adds inferred manager + direct reports (confidence-scored, labeled inference). ~50–100 credits/account — call once |
| `get_tech_stack(domain)` | Sumble confirmed-used tech stack (`GET /organizations/{id}/techs`): technologies verified in use via the org's job posts, grouped by business function/category, with `confirming_job_posts` counts + `ai_coding_tools`. Preferred stack source; ~1 credit per primary technology |
| `get_jobs(domain)` | Apollo job postings (Free plan): title, URL, posted date. Deduped by title; descriptions fetched via `fetch_page` only for eng-relevant roles |
| `get_positioning()` | Read knowledge/ files |
| `refresh_positioning(limit?)` | Incremental refresh (see 4c): returns new/changed items, decides nothing |
| `apply_knowledge_update(...)` | Records classified items; enforces source rules in code (see 4c) |
| `match_customers(companies[])` | Match names against customers.json |
| `get_playbook()` / `add_playbook_rule(rule, scope)` | Feedback memory |
| `add_golden_example(persona, message)` | Store approved messages |
| `lint_message(channel, text, source_ids, subject?)` | Length, banned phrases, public_reference, no layoffs, no "Windsurf", no placeholders; every number must appear in the cited sources |
| `save_brief(domain, brief_json)` / `get_last_brief(domain)` | Validate, persist, diff; renders `.md` + `.docx`; returns report + `brief_markdown` + Slack summary (with the brief link) |

HTTP mode also serves `GET /briefs/<token>/<domain>-<date>.docx`: outside MCP auth (a rep clicks it in Slack), a random token per brief is the access control. `PUBLIC_BASE_URL` (the tunnel / host URL) is required with `--http`.

---

## 1. Company research

**A. Snapshot**

| Field | Source |
|---|---|
| HQ, founded, industry, public/private | Apollo → website |
| **Parent company** | Apollo `owned_by_organization` (e.g. Brex → Capital One). Changes the buying center and procurement path |
| Headcount + 6/12/24mo growth | Apollo |
| Revenue | Filings if public (via Exa); else as reported by Apollo / press, attributed to that source. None → "Not found" |
| Funding, investors, last round | Apollo → press |
| Who they sell to | Website, LLM-summarized |
| Key products | Website |

**Cross-check:** headcount and revenue are checked against a second source (website, press, filings). The JSON keeps every attributed fact. Render picks one working number (prefer the Apollo-attributed fact) and, if sources disagree by more than ~25%, puts the others in a footnote (`Other sources: Tracxn 5,158; TipRanks 5,389.`). Agreeing sources are extra `[n]` markers, not a second number in the cell. (Test: Exa said Brex = 6,570 employees / $283M; Apollo said 1,800 / $700M.)

**B. Engineering & priorities** (the Devin-relevant part)
- Engineering headcount (Apollo department count), open eng roles (Apollo jobs)
- Tech stack (Apollo tech list as a hint, confirmed by job posts / eng blog). AI dev tools in the stack (Cursor, Copilot, Claude) → competitive context in #3
- Eng blog + GitHub links
- AI posture: what leadership has said publicly
- **What they care about:** 2–3 sourced sentences from earnings calls, CEO letters, recent news
- **Peers/competitors:** 3–5 named (feeds peer proof in #4)

**Rules:** every field = value + source + date. "Not found" beats a guess. Third-party figures attributed, never restated as our estimate. Filings are a cross-check, not the freshness source (freshness lives in #3).

---

## 2. Who to work

| Deal role | Typical titles |
|---|---|
| Economic buyer | CTO, SVP/VP Eng, CIO, Chief AI Officer / Head of AI |
| Champion | Dir/Head of Platform, Dev Productivity, DevEx, Eng Enablement; EMs running migrations |
| Technical evaluator | Staff/Principal engineers, architects |
| Gatekeepers | CISO/Security, Procurement (named, not targeted) |

**Entry point by size:** <200 → CTO/founder. Mid-market → VP Eng. Enterprise → CTO/CIO + Dev Productivity/Platform heads, multi-thread.

**Parent company:** if the account is a subsidiary, also identify the relevant parent-org roles (security, procurement, eng leadership) as likely approvers.

**Finding people**
1. Exa people search by role → candidate pool (profiles include "(Current)" flags, dates, past roles, team descriptions).
2. Web pass per candidate for verification + hooks. Per top person: `search_web(..., include_domains=["linkedin.com"])` for posts, talks, interviews (tested: returns real posts/talks/podcasts). A profile without a "(Current)" marker for this company needs a second source before it's listed as current.
3. **Company LinkedIn activity** via `search_company_linkedin` (Exa only — no LinkedIn scraping, no session, no unofficial API): recent company posts when Exa indexed them, best-effort tagged names and reposts from snippet language. Rendered as a "Company LinkedIn" section. A tag becomes a person's hook only when the quote verifies and the person is listed under the normal current-role rules; a tag or repost alone is never a signal. Nothing indexed → "Not found", no placeholder.

**Finding champions** (evidence, not titles)
**Likely champion** if any of:
1. **Ex-employee of a Devin customer** (profile history × customers.json). We cannot prove they personally ran Devin, only that they worked at a Devin customer. Do not invent "used Devin" from an AI title.
2. **Senior title** (Manager+/Staff/Principal/Director/Head, including VP/C-level) **and** a verified public quote about the exact pain (migrations, tech debt, test coverage, DX)
3. **Senior title** **and** named hiring manager on a relevant job post

Supporting or hook only (never the badge):
- `works_on_signal` — on the team behind a strong signal (`who_cares`)
- New in role (<6 months, from profile start date)
- AI appetite (posts about Copilot/Cursor, AI enablement title)

Labels: **Likely champion** vs. **Candidate**. Never "this is your champion."

**Incumbent owner:** someone rolling out or expanding Cursor, Claude Code, Copilot coding agent, or an in-house agent platform. That is a displace/expand risk, not a champion. They can hold both badges (ex-Devin-customer *and* incumbent owner). Copy must not pitch "have you tried agents?"

**Direct reports / wider net:** no asserted reporting lines. People found with a source are grouped by team/function as their own profiles describe it (e.g. "Foundation Eng: Cloud Infra, Dev Tooling, Release Eng"). If a source states a reporting line (job post "reports to the Director of Platform", profile, org announcement), show it with that source.

**Per person:** name, title, deal role, one-line why, LinkedIn URL, verified (second source) vs. profile-only, hooks.

**Listed as current only if** (enforced by the validator): the profile shows a current role at the account or its parent; or a single **official company source** names them (the account's or parent's own domain; or an SEC filing / press-wire release that names the company) — new leaders often haven't updated LinkedIn; or two independent sources do. A single third-party article alone is dropped. The person's name must appear in a source they cite.

**Guardrails:** **3–5 "Who to work" cards** on page one (fewer is fine; empty is "Not found"). Search can still be wide. Keep the top 3 entry points first, then likely champions, incumbent owners, people with a hook, or a signal `who_cares` hit. Drop title-only names from the cards — everyone else is a ZoomInfo export. The validator **caps cards at 5**; extras stay in `people` for the wider table. Prefer verified (profiles can be stale). Never invent a person.

**Page one:** top 3 to contact first + why, rendered as a single **Who to work** section (3–5 cards with deal-role + badges), not four directory heading groups. Default: champion first, multi-thread up to economic buyer.

**Final page — Wider buying center:** a compact table of **every verified person**, one line each (name, title, team, deal role, LinkedIn), grouped by team. Cards are a subset; the table is the net.

---

## 3. Intent signals

**Definition:** evidence they have work Devin could do *today*.

| Type | Example | Source |
|---|---|---|
| Migration / modernization | Java 8→17, AngularJS sunset, COBOL | Job posts, eng blog |
| Backlog / tech debt | "Reduce tech debt", maintenance contractors | Job posts, talks |
| Quality / security | Test coverage push, a vuln *backlog* with scope | Job posts, eng blog |
| AI mandate | "X% more output with AI", "AI-first engineering" | Earnings calls, news, exec posts |
| Leadership change | New CTO/VP Eng <6mo | 8-K, news, profile start dates |
| Hiring composition | Many open roles for migration/maintenance/QA work | Apollo jobs + descriptions + Apollo growth |
| Layoffs/freeze + AI narrative | Cuts explicitly tied to AI/efficiency | News, layoffs.fyi, WARN, earnings |

Contact-level signals from #2 attach to both the person and the account.

**The bar — all five or it's dropped:**
1. Specific (concrete work, not "they use AI")
2. Evidenced (URL + exact quote + date, passes `verify_quotes`)
3. Recent (≤6mo tactical, ≤12mo strategic)
4. Mapped to a Devin use case in knowledge/
5. Usable in a message without sounding creepy or generic

**Excluded by default:** funding alone, generic "hiring engineers," tech stack alone, PR fluff. Plain cost-cut layoffs (no AI tie) = medium at best. A job posting is evidence only when the JD quotes **concrete Devin-shaped work** (a named migration, a coverage target, a vuln backlog with scope). "Hiring a Staff Application Security Engineer" is not a Vulnerability remediation signal. Do not force intent from a job title.

**Cluster, don't count:** 5 job posts about one migration = 1 signal with 5 evidence items.

**Competitive context** (not a signal; changes verdict/angle):
| Situation | Treatment |
|---|---|
| Copilot / autocomplete rollout | Positive — category accepted, pitch agents as the next step |
| Piloting a direct competitor | Urgent — get in before the decision |
| Big direct-competitor deal signed <6mo | **Not now**; note to revisit ~9–12mo after signing |
| Direct competitor in production, no recent deal found | **Warm** by default; angle = displace or expand |

**Per signal:** headline (one line a rep could say), evidence (quotes + links), Devin use case (exact name from `use_cases.json`), who cares (→ contacts), strength (strong / medium; weak is dropped). **Undated evidence can never be strong** (validator caps it at medium).

**Account verdict:** **Act now** / **Warm** / **Not now** (with what to watch for).

---

## 4. Message drafting

**a. Formats (per contact)**
- Email: 4–6 word subject, 75–120 word plain-text body, one ask
- LinkedIn connection note: ≤300 chars
- LinkedIn follow-up DM: ~400–600 chars, doesn't repeat the email

**Angle by deal role:** economic buyer → their mandate/metric + peer proof + one ask; **no product tour**. Devin-usage champion → their specific pain / prior Devin context. Evaluator → how it would run on *their* stack, not the CTO email with a name swap. LinkedIn: do not quote prior-employer trivia unless that trivia **is** the Devin-usage hook. Displace motion: do not say "throw away the pipeline you just published"; join the next wave (remainder, test gaps, harness upkeep). Drafts in the same brief must not share a skeleton.

**b. Using research**
- Structure: about them → why it matters → proof → soft ask
- Must anchor on ≥1 verified signal or hook. None → say so, don't write generic.
- Each draft ships with **"built from"** sources and **Send notes** (rep-only, never sent): 2–5 bullets covering why this angle, channel constraints, what not to say, and whether the LinkedIn follow-up is actually sendable.
- **Peer proof first:** if a peer/competitor from #1 is a public Devin customer, lead with it. Frame as "teams at X use Devin for Y," not FOMO.
- `lint_message` before showing: length, banned phrases ("hope this finds you well", "quick question", "revolutionize"…), only `public_reference: true` customers named, never mention layoffs.

**Product scope:** Devin only (Windsurf has been renamed to Devin; don't position them as two products).

**c. Positioning knowledge** (`knowledge/`, git-tracked, each with `last_updated`)
- `positioning.md` — core narrative, differentiation
- `use_cases.json` — use case → description + proof quotes (JSON so the validator can check signal → use case names exactly)
- `customers.json` — name, proofs (use case, verbatim quote, metric, source_id, URL), `public_reference`
- `sources.json` — sitemaps + include/exclude path rules + third-party queries
- `seen.json` — URLs + lastmod already processed

**Incremental refresh** — never re-crawl:
1. Read the cognition.ai (→ cognition.com) and devin.ai sitemaps; only fetch URLs that are new or whose `lastmod` changed vs `seen.json`. `lastmod` is often a site-rebuild date, so a changed page is only reprocessed if its **content hash** changed. Changed pages are live-crawled (Exa's cache can serve stale or wrong pages).
2. Search third-party Devin mentions only since last refresh date (news, customer eng blogs, earnings calls, job posts mentioning Devin).
3. Classify each new item: **additive** (case study, customer, use case, feature) → append. **Positioning shift** (launch, rebrand, new category claim) → rewrite `positioning.md` + post notice to Slack (optional `SLACK_WEBHOOK_URL`; log only if unset).
4. Git commit with a diff summary. Rollback = revert.

**Source rules** (enforced in `apply_knowledge_update`): core positioning only from Cognition-owned domains (cognition.ai, cognition.com, devin.ai) — a rewrite citing anything else is rejected. Third-party sources can add customers, never change the narrative or add use cases. `public_reference` is computed from the source domain, never set by the model. Every proof quote must verify, and the customer must be named in the page body (not just the title).

**d. Learning from feedback**
- Capture: rep edits (draft vs. sent diff), comments (Slack thread), approve/reject.
- Distill into `playbook/learned_rules.md`: rule + scope (global / persona) + evidence count.
- Approved messages → golden examples per persona (top few), used as few-shot.
- Contradicts an existing rule → ask the rep, don't silently overwrite.
- Agent confirms back: "Learned: X. Applies to: Y."

---

## 5. Additional capabilities

1. **Champion tracking** — ex-employees of Devin customers at the target account (in #2).
2. **Since last run** — rerun diffs against the last saved brief: new signals, new/departed contacts, verdict change. "Nothing new" is a valid answer.

**Deliberately out:** lookalike accounts (account selection, not breaking in), CRM sync, auto-send, call scripts, lead scoring.

---

## Outputs

**Slack thread reply** (what the SDR reads)
```
<Company> — <Verdict>
Why now: <one line>
Signals:
 • <headline> (<source link>)
 • ...
Start with:
 1. <Name>, <Title> — <why>
 2. ...
 3. ...
Full brief: <.docx download link>
```

**Brief** (AE packet = `.docx`; Slack summary = the SDR artifact; Markdown = the git-diffable twin of the same *content*, not the same visual). Rendered in code from the validated JSON. The `.docx` is designed from the record (not converted from Markdown). The model never writes or copies the rendered brief. No Google Docs: Devin's Drive connector needs Google's Drive MCP developer-preview enrollment + OAuth, not worth it for this. The CLI prints the local path; HTTP mode links the .docx.
1. Since last run (if applicable)
2. Verdict badge + one-line why + watch-for
3. Compact snapshot (one working number each)
4. Engineering & priorities (Markdown; `.docx` folds eng size + stack into the snapshot)
5. Intent signals (+ competitive context)
6. **Who to work** (3–5 people as cards: deal-role + likely-champion / incumbent-owner badges)
7. Recommended entry: top 3
8. Drafted messages (email + LinkedIn + send notes per contact, with "built from")
9. ABM themes (2–3 account-level angles for marketing)
10. **Wider buying center** (final page: compact table of every verified person, grouped by team)
11. Sources (receding; not page one)

**Source of truth:** `briefs/<domain>/<date>.json`; the `.md`, `.docx` and Slack summary are renders of it. The Slack reply is the tool-rendered `slack_summary`, posted verbatim; agent prose outside the brief/summary carries no facts (it isn't validated).

---

## Reproducibility

- Two cache layers in `cache/`:
  - **requests/**: raw provider response keyed by endpoint + request; maps to the doc IDs it returned.
  - **docs/**: one file per citable document (`source_id`): web page, people profile, job post, Apollo org record (cited as "Apollo, retrieved <date>").
- TTL per entry: discovery calls (all Exa searches, Apollo job list) = 1 day; lookups of known things (fetched pages, Apollo firmographics) = 7 days. Reruns reuse fresh entries, refetch expired ones. `--replay` = cache only (committed demo); `--fresh` = refetch everything.
- `cache/` is gitignored; the demo account's cache is committed once with `git add -f`.
- Demo account's cache committed so reviewers can reproduce without API spend.
- `.env.example` lists keys: `ANTHROPIC_API_KEY` (runner; or an existing Claude Code login), `EXA_API_KEY`, `APOLLO_API_KEY` (Free plan is enough). HTTP mode adds `MCP_AUTH_TOKEN` and `PUBLIC_BASE_URL`. No Google credentials.

---

## Write-up notes

- The validated JSON in `briefs/` is the canonical record; Markdown, .docx and Slack are renders in code, never sources.
- Draft claims: every prospect-specific claim in a draft must be listed with a verbatim quote, and each is verified (one failure blocks the draft); numbers, digits or words, must appear in the cited sources. A real quote can still fail to support its claim (run 3: "runs in a Bazel monorepo" cited to a quote about Cursor agents), so every capitalized term and number in a claim must also appear in its own quote (deterministic; exempt: prospect, contact, Devin, sentence-initial words; a small abbreviation map, PR ↔ pull request). Deliberately over-blocks. Residual limits: lowercase terms aren't checked ("monorepo"), and we can't prove every claim in the prose was listed. The rep sees each claim with its quote under the draft. **Production next step: an LLM judge (claim entailed by quote?) as a separate review pass, kept out of the deterministic validator.**
- **Likely champion** is checkable: confirmed `ex_devin_customer` (profile history × `customers.json`); or a senior profile title (Manager+/Staff/Principal/Director/Head, including VP/C-level) plus a verified `public_pain` quote; or senior plus `hiring_manager`. `works_on_signal` is checkable (the person is in a strong signal's `who_cares`) but never earns the badge — supporting or hook only, same as `new_in_role` and `ai_appetite`. Honest limit on ex-customer: we cannot prove they personally ran Devin.
- Only the brief is validated. Agent prose outside it is unchecked, so the playbook forbids facts there and the Slack reply is the tool-rendered summary posted verbatim (in Devin this relies on the agent following the playbook; the CLI enforces it in code).

## Production notes (for write-up / Loom)

- **Salesforce closed-won** replaces the public customer list: complete, includes non-public customers for internal champion tracking and broader peer matching.
- **Reply / meeting outcomes** become the primary feedback signal (beyond edits).
- **Per-rep rule scope**, promoted to global when several reps agree.
- **Scheduled runs** (Agent SDK, headless) for weekly "since last run" on the target list.
- **Google Workspace:** a service account writes the same rendered brief into a shared team Drive folder as a Google Doc (no per-rep OAuth); the link replaces the .docx download.
- **Data provider is swappable** — point at whatever Cognition already pays for (ZoomInfo, Sales Nav, etc.).
- **Lookalikes** — same customer + firmographic data could feed a marketing list builder.

---

## Open items to verify before build

- [x] Apollo Free plan: org enrich + job postings work via API (tested on brex.com). People search / people match are paid-only → people come from Exa
- [x] Exa people search: accurate, current profiles with history + start dates (tested on Brex). Exa company data unreliable → Apollo for firmographics
- [x] Devin: custom MCP supported (stdio / SSE / HTTP); Playbooks invokable via `!macro`
- [x] Devin: custom MCP available in Slack-triggered sessions (per Devin). Needs a public HTTPS URL; added via Customize → MCPs → Add custom MCP, bearer header, org scope. Validate with MCP Inspector first; confirm with a real Slack run.
- [ ] Claude in Slack: confirm custom connectors + skills work there
- [x] Google Docs: dropped. Devin's Drive connector needs Google's Drive MCP developer-preview enrollment + OAuth. The server renders a .docx and serves it at a per-brief link instead (see Production notes for Google Workspace)
- [x] Exa: `startPublishedDate` supports incremental search (use only for bounded windows, per Exa guidance)
- [x] Demo account choice: brex.com (not a Devin customer, strong public eng footprint)
