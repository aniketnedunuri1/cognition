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

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.debug import DebugTokenVerifier
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response

from . import apollo, briefs, exa, feedback, knowledge, lint, verify
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
    signal recency windows (6 / 12 months); for "recent" just say so in the query.
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
def get_company(domain: str) -> dict:
    """Apollo firmographics: HQ, founded, industry, parent company, headcount + growth,
    department headcount, revenue, funding, AI tools, engineering tech stack.
    All values are Apollo's report: attribute them ("Apollo reports ...").
    eng_tech_stack_hint is a hint: state a technology as fact only when a job post,
    eng blog, or profile confirms it."""
    return apollo.get_company(domain)


@_tool
def get_jobs(domain: str, eng_only: bool = True) -> dict:
    """Apollo job postings, deduped by title (each posting has its own source_id).
    No descriptions: fetch_page(source_id) the engineering-relevant ones you need."""
    return apollo.get_jobs(domain, eng_only)


@_tool
def verify_quote(source_id: str, quote: str) -> dict:
    """Check that `quote` appears verbatim in the cached source `source_id`.
    Use "..." to join parts that appear in order. A quote cited to the wrong source fails."""
    return verify.verify_quote(source_id, quote)


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
def save_brief(domain: str, brief: dict) -> dict:
    """Validate and save the brief (shape: playbook Output contract). Returns the validator
    report (what was dropped and why), the since-last-run diff, the rendered brief_markdown,
    and the Slack summary (its "Full brief" is the .docx link). Fix drops with real evidence —
    never by rewording — then save again. Post the final slack_summary verbatim."""
    return briefs.save_brief(domain, brief)


@_tool
def get_last_brief(domain: str) -> dict:
    """The most recently saved brief for this domain, for since-last-run comparison."""
    return briefs.get_last_brief(domain)


@_tool
def get_playbook() -> dict:
    """Learned rules (apply all that match the stage and persona) and golden examples
    (style references for drafting; never copy their facts)."""
    return feedback.get_playbook()


@_tool
def add_playbook_rule(rule: str, scope: str, replaces: str | None = None) -> dict:
    """Record a rule distilled from rep feedback. scope: "global" or "persona:<deal_role>".
    Check get_playbook first: if it contradicts an existing rule, ask the rep, and pass
    the superseded rule's text as `replaces` only if they say the new one wins.
    Factual corrections are not rules — fix the brief instead."""
    return feedback.add_playbook_rule(rule, scope, replaces)


@_tool
def add_golden_example(persona: str, channel: str, message: str, subject: str | None = None) -> dict:
    """Store a rep-approved message as a style reference. persona: deal role;
    channel: email | linkedin_note | linkedin_followup."""
    return feedback.add_golden_example(persona, channel, message, subject)


TOOLS = [search_web, fetch_page, search_people, get_company, get_jobs, verify_quote,
         get_positioning, match_customers, refresh_positioning, apply_knowledge_update,
         lint_message, save_brief, get_last_brief,
         get_playbook, add_playbook_rule, add_golden_example]


def build_server(auth_token: str | None = None) -> FastMCP:
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
        briefs.BASE_URL = public
        build_server(token).run(transport="http", host=args.host, port=args.port)
    else:
        build_server().run()


if __name__ == "__main__":
    main()
