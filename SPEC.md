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
5. **Conflicts are shown, not resolved.** Two sources disagree → show both, flag it.
6. **Judgment is separated from fact.** Verdict, "likely champion," recommended entry point, and ABM themes are labeled as the agent's assessment and cite the facts they rest on. Nothing inferred is presented as fact (e.g. no asserted reporting lines).
7. **Enforced in code, not just the prompt.** Every tool result carries a `source_id` per document; the model cites `source_id`s only, never URLs. The validator resolves each against the cache and drops (and counts) anything that doesn't resolve; `verify_quote` checks every quote against the cached full text of the cited source; people must be named in a source they cite. Real URLs + retrieved dates are filled in from the cache at render time.

---

## User loop

1. Rep: `@Devin research acme.com` in Slack (or `python agent.py research acme.com` locally).
2. Agent runs company → people → signals, writes the brief to a Google Doc.
3. Agent replies in thread with a short summary + doc link.
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
| `search_web(query, since?)` | Exa `/search`, cached. Covers blogs, talks, news, earnings transcripts, filings, GitHub, Devin mentions |
| `fetch_page(url_or_source_id, offset?)` | Exa `/contents`, cached. Full text cached; model gets a 6k-char excerpt (page with `offset`). Job descriptions and any known URL |
| `search_people(company, roles)` | Exa `/search` with `category: "people"`: current title, history, start dates, team descriptions |
| `verify_quote(source_id, quote)` | Deterministic check that a quote exists in that source's cached full text (a quote cited to the wrong source fails) |
| `get_company(domain)` | Apollo org enrichment (Free plan): firmographics, parent org, revenue, dept headcount, 6/12/24mo growth, funding, AI tools, engineering tech stack (eng categories only; a hint until a job post / blog / profile confirms it) |
| `get_jobs(domain)` | Apollo job postings (Free plan): title, URL, posted date. Deduped by title; descriptions fetched via `fetch_page` only for eng-relevant roles |
| `get_positioning()` | Read knowledge/ files |
| `refresh_positioning(limit?)` | Incremental refresh (see 4c): returns new/changed items, decides nothing |
| `apply_knowledge_update(...)` | Records classified items; enforces source rules in code (see 4c) |
| `match_customers(companies[])` | Match names against customers.json |
| `get_playbook()` / `add_playbook_rule(rule, scope)` | Feedback memory |
| `add_golden_example(persona, message)` | Store approved messages |
| `lint_message(channel, text, source_ids, subject?)` | Length, banned phrases, public_reference, no layoffs, no "Windsurf", no placeholders; every number must appear in the cited sources |
| `save_brief(domain, brief_json)` / `get_last_brief(domain)` | Persistence + diffing |
| `create_brief_doc(domain)` | Google Doc in the rep's own Drive (one-time user OAuth, `drive.file` scope, app-created folder) for the latest saved brief; re-runs update the same Doc; returns link + Slack summary |

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

**Cross-check:** headcount and revenue are checked against a second source (website, press, filings). If sources disagree materially, show both and flag it. (Test: Exa said Brex = 6,570 employees / $283M; Apollo said 1,800 / $700M.)

**B. Engineering & priorities** (the Devin-relevant part)
- Engineering headcount (Apollo department count), open eng roles (Apollo jobs)
- Tech stack (Apollo tech list as a hint, confirmed by job posts / eng blog). AI dev tools in the stack (Cursor, Copilot, Claude) → competitive context in #3
- Eng blog + GitHub links
- AI posture: what leadership has said publicly
- **What they care about:** 2–3 sourced sentences from earnings calls, CEO letters, recent news
- **Peers/competitors:** 3–5 named (feeds peer proof in #4)

**Rules:** every field = value + source + date. "Not found" beats a guess. Third-party figures attributed, never restated as our estimate. Filings are a cross-check, not the freshness source (freshness lives in #3).

---

## 2. Buying centers & contacts

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
2. Web pass per candidate for verification + hooks (blog posts, talks, podcasts, press quotes, GitHub, indexed LinkedIn posts). A profile without a "(Current)" marker for this company needs a second source before it's listed as current.

**Finding champions** (evidence, not titles)
1. Named as hiring manager / team lead in relevant job posts
2. Has publicly discussed the pain (migrations, tech debt, test coverage, DX)
3. AI appetite (posts about Copilot/Cursor, AI enablement title)
4. New in role (<6 months, from profile start date)
5. **Ex-employee of a Devin customer** (profile history × customers.json) — champion tracking

Labels: **Likely champion** (≥1 evidence item, linked) vs. **Candidate** (title only). Never "this is your champion."

**Direct reports / wider net:** no asserted reporting lines. People found with a source are grouped by team/function as their own profiles describe it (e.g. "Foundation Eng: Cloud Infra, Dev Tooling, Release Eng"). If a source states a reporting line (job post "reports to the Director of Platform", profile, org announcement), show it with that source.

**Per person:** name, title, deal role, one-line why, LinkedIn URL, verified (second source) vs. profile-only, hooks.

**Listed as current only if** (enforced by the validator): the profile shows a current role at the account or its parent; or a single **official company source** names them (the account's or parent's own domain; or an SEC filing / press-wire release that names the company) — new leaders often haven't updated LinkedIn; or two independent sources do. A single third-party article alone is dropped. The person's name must appear in a source they cite.

**Guardrails:** ~15–25 people max. Prefer verified (profiles can be stale). Never invent a person.

**Ends with:** top 3 to contact first + why. Default: champion first, multi-thread up to economic buyer.

---

## 3. Intent signals

**Definition:** evidence they have work Devin could do *today*.

| Type | Example | Source |
|---|---|---|
| Migration / modernization | Java 8→17, AngularJS sunset, COBOL | Job posts, eng blog |
| Backlog / tech debt | "Reduce tech debt", maintenance contractors | Job posts, talks |
| Quality / security | Test coverage push, vuln remediation roles | Job posts, eng blog |
| AI mandate | "X% more output with AI", "AI-first engineering" | Earnings calls, news, exec posts |
| Leadership change | New CTO/VP Eng <6mo | 8-K, news, profile start dates |
| Hiring composition | Many open roles for migration/maintenance/QA work | Apollo jobs + descriptions + Apollo growth |
| Layoffs/freeze + AI narrative | Cuts explicitly tied to AI/efficiency | News, layoffs.fyi, WARN, earnings |

Contact-level signals from #2 attach to both the person and the account.

**The bar — all five or it's dropped:**
1. Specific (concrete work, not "they use AI")
2. Evidenced (URL + exact quote + date, passes `verify_quote`)
3. Recent (≤6mo tactical, ≤12mo strategic)
4. Mapped to a Devin use case in knowledge/
5. Usable in a message without sounding creepy or generic

**Excluded by default:** funding alone, generic "hiring engineers," tech stack alone, PR fluff. Plain cost-cut layoffs (no AI tie) = medium at best.

**Cluster, don't count:** 5 job posts about one migration = 1 signal with 5 evidence items.

**Competitive context** (not a signal; changes verdict/angle):
| Situation | Treatment |
|---|---|
| Copilot / autocomplete rollout | Positive — category accepted, pitch agents as the next step |
| Piloting a direct competitor | Urgent — get in before the decision |
| Big direct-competitor deal signed <6mo | **Not now**; note to revisit ~9–12mo after signing |

**Per signal:** headline (one line a rep could say), evidence (quotes + links), Devin use case (exact name from `use_cases.json`), who cares (→ contacts), strength (strong / medium; weak is dropped). **Undated evidence can never be strong** (validator caps it at medium).

**Account verdict:** **Act now** / **Warm** / **Not now** (with what to watch for).

---

## 4. Message drafting

**a. Formats (per contact)**
- Email: 4–6 word subject, 75–120 word plain-text body, one ask
- LinkedIn connection note: ≤300 chars
- LinkedIn follow-up DM: ~400–600 chars, doesn't repeat the email

**Angle by deal role:** economic buyer → mandate + peer proof. Champion → their specific pain. Evaluator → technical how-it-works.

**b. Using research**
- Structure: about them → why it matters → proof → soft ask
- Must anchor on ≥1 verified signal or hook. None → say so, don't write generic.
- Each draft ships with **"built from"** notes (signals + sources) for the rep.
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
- Capture: rep edits (draft vs. sent diff), comments (Slack thread / Google Doc), approve/reject.
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
Full brief: <Google Doc link>
```

**Google Doc brief** (AE + marketing; comments feed back into learning)
1. Since last run (if applicable)
2. Verdict + why now
3. Snapshot
4. Engineering & priorities
5. Intent signals (+ competitive context)
6. Buying centers (by deal role; likely champions flagged; grouped by team)
7. Recommended entry: top 3
8. Drafted messages (email + LinkedIn per contact, with "built from")
9. ABM themes (2–3 account-level angles for marketing)
10. Sources

**Source of truth:** `briefs/<domain>/<date>.json` + `.md`; Doc and Slack are renders of it. The Slack reply is the tool-rendered `slack_summary`, posted verbatim; agent prose outside the brief/summary carries no facts (it isn't validated).

---

## Reproducibility

- Two cache layers in `cache/`:
  - **requests/**: raw provider response keyed by endpoint + request; maps to the doc IDs it returned.
  - **docs/**: one file per citable document (`source_id`): web page, people profile, job post, Apollo org record (cited as "Apollo, retrieved <date>").
- TTL per entry: discovery calls (all Exa searches, Apollo job list) = 1 day; lookups of known things (fetched pages, Apollo firmographics) = 7 days. Reruns reuse fresh entries, refetch expired ones. `--replay` = cache only (committed demo); `--fresh` = refetch everything.
- `cache/` is gitignored; the demo account's cache is committed once with `git add -f`.
- Demo account's cache committed so reviewers can reproduce without API spend.
- `.env.example` lists keys: `ANTHROPIC_API_KEY` (runner; or an existing Claude Code login), `EXA_API_KEY`, `APOLLO_API_KEY` (Free plan is enough). Google Docs: optional one-time OAuth (`python -m server.gdocs auth`).

---

## Production notes (for write-up / Loom)

- **Salesforce closed-won** replaces the public customer list: complete, includes non-public customers for internal champion tracking and broader peer matching.
- **Reply / meeting outcomes** become the primary feedback signal (beyond edits).
- **Per-rep rule scope**, promoted to global when several reps agree.
- **Scheduled runs** (Agent SDK, headless) for weekly "since last run" on the target list.
- **Data provider is swappable** — point at whatever Cognition already pays for (ZoomInfo, Sales Nav, etc.).
- **Lookalikes** — same customer + firmographic data could feed a marketing list builder.

---

## Open items to verify before build

- [x] Apollo Free plan: org enrich + job postings work via API (tested on brex.com). People search / people match are paid-only → people come from Exa
- [x] Exa people search: accurate, current profiles with history + start dates (tested on Brex). Exa company data unreliable → Apollo for firmographics
- [x] Devin: custom MCP supported (stdio / SSE / HTTP); Playbooks invokable via `!macro`
- [x] Devin: custom MCP available in Slack-triggered sessions (per Devin). Needs a public HTTPS URL; added via Customize → MCPs → Add custom MCP, bearer header, org scope. Validate with MCP Inspector first; confirm with a real Slack run.
- [ ] Claude in Slack: confirm custom connectors + skills work there
- [x] Google Docs: one-time user OAuth into the rep's own Drive (`drive.file`); no Workspace needed. Doc comments as a feedback source: not built yet
- [x] Exa: `startPublishedDate` supports incremental search (use only for bounded windows, per Exa guidance)
- [ ] Demo account choice (not a Devin customer, strong public eng footprint)
