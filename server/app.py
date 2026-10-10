"""MCP server: deterministic data tools. No judgment lives here.

    python -m server.app                 # stdio (CLI runner, Claude Code)
    python -m server.app --http          # Streamable HTTP (Devin): MCP_AUTH_TOKEN + PUBLIC_BASE_URL
    python -m server.app --replay        # cache only (committed demo)
"""

from __future__ import annotations

import argparse
import functools
import hmac
import os
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.debug import DebugTokenVerifier
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response

from . import apollo, briefs, exa, feedback, knowledge, lint, sumble, verify
from .cache import ROOT, CacheMiss

INSTRUCTIONS = """Account research data tools. Every result carries a source_id per document.
Cite facts by source_id only; never write URLs or dates yourself — they are filled in from the cache.
If a tool returns nothing, the answer is "Not found". Your own knowledge is never a source."""


def _tool(fn):
    """Turn provider/cache failures into tool errors the model can read."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (exa.ProviderError, CacheMiss) as e:
            raise ToolError(str(e)) from e
    return wrapper


@_tool
def search_web(query: str, since: str | None = None,
               include_domains: list[str] | None = None) -> list[dict]:
    """Search the web (blogs, talks, news, earnings transcripts, filings, GitHub).
    Returns per result: source_id, title, site, published_date, highlights.
    since: optional YYYY-MM-DD hard cutoff on publication date — use only for the
    signal recency window (6 months); for "recent" just say so in the query.
    include_domains: hard allowlist (e.g. ["linkedin.com"] for posts, talks, interviews)."""
    return exa.search_web(query, since, include_domains)


@_tool
def fetch_page(url_or_source_id: str, offset: int = 0, force: bool = False) -> dict:
    """Fetch a page's text (job descriptions, articles, company website).
    Pass a source_id from another tool, or a URL you got from a tool result or the rep.
    Returns an excerpt of up to 6000 chars; call again with next_offset for more.
    status "not_fetched" means the page couldn't be read — treat it as Not found.
    force=True live-crawls the page: use only when the content looks wrong for that URL
    (e.g. a different company's story) or is clearly stale."""
    return exa.fetch_page(url_or_source_id, offset, force)


@_tool
def search_people(company: str, roles: list[str]) -> dict:
    """Find people by role at a company (one search per role, merged by profile; 3-5 roles
    per call). people: current_title, is_current (true / "unknown"), start_date,
    months_in_role, previous_role, ex_devin_customers (past employers that are Devin
    customers; name one only if public_reference is true), a short highlight.
    is_current "unknown": list only with a second source or an official company source.
    Former employees are only counted: never list them as current."""
    return exa.search_people(company, roles)


@_tool
def search_company_linkedin(company: str, domain: str | None = None,
                            linkedin_slug: str | None = None, since: str | None = None) -> dict:
    """Company LinkedIn activity via Exa search only (no scraping). Returns whatever Exa
    indexed: original posts labeled "original" when the company slug is known (from
    linkedin_slug, else the cached Apollo org's linkedin_url for domain), repost-like hits
    labeled "possible_repost" from snippet language only, the rest "unknown".
    possible_tags are /in/ links and @names that literally appear in the snippet — not a
    mentions graph. No slug is ever invented; without one the searches fall back to the
    whole linkedin.com domain. Empty results and per-query not_fetched are normal."""
    return exa.search_company_linkedin(company, domain, linkedin_slug, since)


@_tool
def get_company(domain: str) -> dict:
    """Apollo firmographics: HQ, founded, industry, parent company, headcount + growth,
    department headcount, revenue, funding, AI tools, engineering tech stack.
    All values are Apollo's report: attribute them ("Apollo reports ...").
    eng_tech_stack_hint is website tags — a fallback/hint for when get_tech_stack
    finds nothing; state a technology as fact only when a job post, eng blog,
    or profile confirms it."""
    return apollo.get_company(domain)


@_tool
def get_jobs(domain: str, eng_only: bool = True) -> dict:
    """Apollo job postings, deduped by title (each posting has its own source_id).
    No descriptions: fetch_page(source_id) the engineering-relevant ones you need."""
    return apollo.get_jobs(domain, eng_only)


@_tool
def get_org_chart(domain: str, people: list[dict] | None = None) -> dict:
    """Sumble org chart anchored on the sourced people list. Pass people as
    [{name, linkedin_url}] (from search_people profiles): each is matched to a
    Sumble person by LinkedIn URL (unique exact name as fallback) and gets their
    inferred manager + up to 5 direct reports (confidence ≥0.4 shown).
    decision_makers[].matched = false means Sumble picked them, not your pipeline —
    label them "Sumble only, not yet sourced". unmatched_people lists passed names
    with no Sumble match. Reporting lines are Sumble's inference — never confirmed;
    attribute to Sumble and label inferred everywhere they appear.
    Costs ~50-100 Sumble credits per account: required — call exactly once per
    account. To render, pass source_id as brief.org_chart."""
    return sumble.get_org_chart(domain, people)


@_tool
def get_tech_stack(domain: str) -> dict:
    """Sumble confirmed-used tech stack (GET /organizations/{id}/techs): technologies
    verified in use via the org's job posts — confirming_job_posts is the count of
    posts confirming use, not mentions. Grouped by business function and category,
    plus ai_coding_tools. Prefer this over Apollo's eng_tech_stack_hint (website
    tags): attribute as "Sumble reports ...". ~1 credit per primary technology."""
    return sumble.get_tech_stack(domain)


@_tool
def verify_quotes(quotes: list[dict]) -> list[dict]:
    """Check quotes appear verbatim in the cached sources they cite. Pass every quote
    in one call: [{source_id, quote}, ...]. Use "..." to join parts that appear in
    order. A quote cited to the wrong source fails."""
    return verify.verify_quotes(quotes)


@_tool
def get_positioning() -> dict:
    """Devin positioning, use cases (with proof quotes + source_ids), and the customers
    that may be named in outreach (public_reference). Map every signal to a use case here."""
    return knowledge.get_positioning()


@_tool
def match_customers(companies: list[str]) -> list[dict]:
    """Which of these companies are Devin customers. Use for peer proof (account's peers)
    and champion tracking (people's past employers). public_reference false = may inform
    targeting, never named in a message."""
    return knowledge.match_customers(companies)


@_tool
def refresh_positioning(limit: int = 15) -> dict:
    """Find new/changed Cognition pages (sitemap diff vs. last run) and third-party Devin
    mentions since the last refresh. Returns items to classify; decides nothing.
    Record results with apply_knowledge_update, then call again until items is empty."""
    return knowledge.refresh_positioning(limit)


@_tool
def apply_knowledge_update(processed_source_ids: list[str], summary: str,
                           customers: list[dict] | None = None, use_cases: list[dict] | None = None,
                           positioning_markdown: str | None = None) -> dict:
    """Record classified refresh items. Pass every reviewed source_id in processed_source_ids,
    including ones with nothing to add.
    customers: [{name, use_case, quote, source_id, metric?}] — quote verbatim from the source.
    use_cases: [{name, description, quote, source_id, customer?}] — reuse existing names.
    positioning_markdown: full rewrite ONLY for a positioning shift (launch, rebrand, new
    category claim); cite claims as [src_...] from Cognition-owned sources only.
    Additive items never rewrite positioning. public_reference is computed, not set."""
    return knowledge.apply_knowledge_update(processed_source_ids, summary, customers, use_cases,
                                            positioning_markdown)


@_tool
def lint_message(channel: str, text: str, source_ids: list[str] | None = None,
                 subject: str | None = None) -> dict:
    """Check a draft before showing it. channel: email (pass subject) | linkedin_note |
    linkedin_followup. source_ids: the draft's built-from sources — every number in the
    message must appear in one of them. Fix every issue and re-lint until ok is true."""
    return lint.lint_message(channel, text, source_ids, subject)


@_tool
def whoami() -> dict:
    """The rep identity for this session, for the `rep` argument on get_playbook,
    add_playbook_rule, add_golden_example and save_brief. Decoded from the session's
    OIDC identity token (requesting_user_email) when present, else the REP env var.
    rep is None when identity can't be resolved — ask the rep for their Slack handle."""
    import base64
    import json as _json
    for var in ("DEVIN_OIDC_TOKEN", "DEVIN_IDENTITY_TOKEN", "OIDC_TOKEN", "DEVIN_IDENTITY_JWT"):
        token = os.environ.get(var)
        if not token or token.count(".") != 2:
            continue
        try:
            payload = _json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        except Exception:
            continue
        email = payload.get("requesting_user_email") or payload.get("email")
        if email:
            return {"rep": feedback.rep_slug(email), "source": var}
    rep = os.environ.get("REP") or os.environ.get("REP_ID")
    return {"rep": feedback.rep_slug(rep), "source": "env" if rep else "none"}


@_tool
def save_brief(domain: str, brief: dict, rep: str | None = None) -> dict:
    """Validate and save the brief (shape: playbook Output contract). Returns the validator
    report (what was dropped and why), the since-last-run diff, the rendered brief_markdown,
    and the Slack summary (its "Full brief" is the .docx link). Fix drops with real evidence —
    never by rewording — then save again. Post the final slack_summary verbatim.
    rep: the requesting rep's handle (from whoami) — scopes the file so two reps can
    brief the same account the same day and tags their report comments."""
    return briefs.save_brief(domain, brief, rep)


@_tool
def get_last_brief(domain: str) -> dict:
    """The most recently saved brief for this domain, for since-last-run comparison."""
    return briefs.get_last_brief(domain)


@_tool
def get_brief_feedback(domain: str) -> dict:
    """Unprocessed rep comments left on the HTML report (oldest brief first). Call at
    Stage 0 before drafting: distill each into a rule via add_playbook_rule (generalizable)
    or treat as a data fix (factual correction), then mark_brief_feedback_processed.
    Comment tags: "applies always" hints at a global rule; "keep this" is approval —
    a keep-doing rule or add_golden_example when the highlight covers a whole message."""
    return briefs.get_brief_feedback(domain)


@_tool
def mark_brief_feedback_processed(domain: str, comment_ids: list[str]) -> dict:
    """Mark report comments handled after distilling each into a rule or judging it a
    factual fix / non-rule feedback. Only mark ids you have actually dealt with."""
    return briefs.mark_brief_feedback_processed(domain, comment_ids)


@_tool
def get_playbook(rep: str | None = None) -> dict:
    """Rules and golden examples for this run. Pass the rep's handle (whoami) so their
    own rules are included. Layers: locked_rules (policy, never overridable) →
    learned_rules (team defaults) → rep_rules (the rep's own, their drafts only).
    Golden examples are style references for drafting; never copy their facts."""
    return feedback.get_playbook(rep)


@_tool
def add_playbook_rule(rule: str, scope: str, rep: str | None = None,
                      replaces: str | None = None) -> dict:
    """Record a rule distilled from rep feedback. scope: "global" or "persona:<deal_role>".
    `rep` is required — the sender's handle from whoami / the Slack trigger, never
    self-declared in message text. The rule lands in their own layer (their drafts
    only); anonymous writes are rejected. When several reps independently converge on
    the same rule it is promoted to team defaults via a reviewed PR.
    Check get_playbook first: if it contradicts one of that rep's own rules, ask them,
    and pass the superseded rule's text as `replaces` only if they say the new one wins.
    Contradicting a team default is fine — the rep's rule just wins for them. Locked
    rules can't be replaced.
    Factual corrections are not rules — fix the brief instead."""
    return feedback.add_playbook_rule(rule, scope, rep, replaces)


@_tool
def add_golden_example(persona: str, channel: str, message: str, subject: str | None = None,
                       rep: str | None = None) -> dict:
    """Store a rep-approved message as a style reference. persona: deal role;
    channel: email | linkedin_note | linkedin_followup. `rep` is required — the
    approving rep's handle; their examples become their own style references."""
    return feedback.add_golden_example(persona, channel, message, subject, rep)


TOOLS = [search_web, fetch_page, search_people, search_company_linkedin, get_company, get_jobs,
         get_org_chart, get_tech_stack, verify_quotes, whoami,
         get_positioning, match_customers, refresh_positioning, apply_knowledge_update,
         lint_message, save_brief, get_last_brief,
         get_playbook, add_playbook_rule, add_golden_example,
         get_brief_feedback, mark_brief_feedback_processed]


def public_host_origin(public_base_url: str) -> tuple[list[str], list[str]]:
    """Hostname + origin FastMCP should allow for a public tunnel URL.

    FastMCP 4's HostOriginGuardMiddleware (``allowed_hosts`` / ``allowed_origins``,
    enabled via ``host_origin_protection``) rejects requests whose Host header
    isn't on the list — a cloudflared hostname would 421 without this.
    """
    parsed = urlsplit(public_base_url.strip())
    host = parsed.hostname
    if not host:
        raise ValueError(f"PUBLIC_BASE_URL has no hostname: {public_base_url!r}")
    origin = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    return [host], [origin]


def _allow_public_host(mcp: FastMCP, public_base_url: str) -> None:
    """So ``http_app()`` (tests) and ``run()`` both get the tunnel on the allowlist."""
    hosts, origins = public_host_origin(public_base_url)
    inner = mcp.http_app

    def http_app(*args, **kwargs):
        if not kwargs.get("allowed_hosts"):
            kwargs["allowed_hosts"] = hosts
        if not kwargs.get("allowed_origins"):
            kwargs["allowed_origins"] = origins
        if not kwargs.get("host_origin_protection"):
            kwargs["host_origin_protection"] = True
        return inner(*args, **kwargs)

    mcp.http_app = http_app  # type: ignore[method-assign]


def build_server(auth_token: str | None = None, public_base_url: str | None = None) -> FastMCP:
    auth = None
    if auth_token:
        auth = DebugTokenVerifier(validate=lambda t: hmac.compare_digest(t, auth_token))
    mcp = FastMCP("account-research", instructions=INSTRUCTIONS, tools=TOOLS, auth=auth)

    # The brief link in the Slack summary. Outside MCP auth (a rep clicks it in Slack):
    # the per-brief random token in the path is the access control.
    @mcp.custom_route("/briefs/{token}/{filename}", methods=["GET"], include_in_schema=False)
    async def download_brief(request: Request) -> Response:
        path = briefs.find_download(request.path_params["token"])
        if not path:
            return PlainTextResponse("Not found", status_code=404)
        return FileResponse(path, filename=f"{path.parent.name}-{path.stem}.docx", media_type=DOCX)

    # The annotatable report. Same token access control as the .docx link.
    @mcp.custom_route("/report/{token}", methods=["GET"], include_in_schema=False)
    async def view_report(request: Request) -> Response:
        path = briefs.find_report(request.path_params["token"])
        if not path:
            return PlainTextResponse("Not found", status_code=404)
        return FileResponse(path, media_type="text/html")

    @mcp.custom_route("/report/{token}/comments", methods=["POST"], include_in_schema=False)
    async def report_comments(request: Request) -> Response:
        try:
            body = await request.json()
            comments = body.get("comments")
            assert isinstance(comments, list)
        except Exception:
            return PlainTextResponse("Bad request", status_code=400)
        saved = briefs.save_comments(request.path_params["token"], comments, body.get("rep"))
        if not saved:
            return PlainTextResponse("Not found", status_code=404)
        from starlette.responses import JSONResponse
        return JSONResponse(saved)

    if public_base_url:
        _allow_public_host(mcp, public_base_url)
    return mcp


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def main() -> None:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="Streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    cache = ap.add_mutually_exclusive_group()
    cache.add_argument("--replay", action="store_true", help="cache only, no API calls")
    cache.add_argument("--fresh", action="store_true", help="ignore cache, refetch everything")
    args = ap.parse_args()

    if args.replay:
        os.environ["CACHE_MODE"] = "replay"
    elif args.fresh:
        os.environ["CACHE_MODE"] = "fresh"

    if args.http:
        token, public = os.environ.get("MCP_AUTH_TOKEN"), os.environ.get("PUBLIC_BASE_URL")
        if not token or not public:
            raise SystemExit("--http needs MCP_AUTH_TOKEN and PUBLIC_BASE_URL (the tunnel's https URL)")
        try:
            hosts, origins = public_host_origin(public)
        except ValueError as e:
            raise SystemExit(str(e)) from e
        briefs.BASE_URL = public
        build_server(token, public_base_url=public).run(
            transport="http", host=args.host, port=args.port,
            allowed_hosts=hosts, allowed_origins=origins, host_origin_protection=True,
        )
    else:
        build_server().run()


if __name__ == "__main__":
    main()
