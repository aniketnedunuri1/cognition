"""MCP server: deterministic data tools. No judgment lives here.

    python -m server.app                 # stdio (CLI runner, Claude Code)
    python -m server.app --http          # Streamable HTTP (Devin), bearer token required
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

from . import apollo, exa, verify
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
def search_web(query: str, since: str | None = None) -> list[dict]:
    """Search the web (blogs, talks, news, earnings transcripts, filings, GitHub).
    Returns per result: source_id, title, site, published_date, highlights.
    since: optional YYYY-MM-DD hard cutoff on publication date — use only for the
    signal recency windows (6 / 12 months); for "recent" just say so in the query."""
    return exa.search_web(query, since)


@_tool
def fetch_page(url_or_source_id: str, offset: int = 0) -> dict:
    """Fetch a page's text (job descriptions, articles, company website).
    Pass a source_id from another tool, or a URL you got from a tool result or the rep.
    Returns an excerpt of up to 6000 chars; call again with next_offset for more.
    status "not_fetched" means the page couldn't be read — treat it as Not found."""
    return exa.fetch_page(url_or_source_id, offset)


@_tool
def search_people(company: str, roles: list[str]) -> list[dict]:
    """Find people by role at a company (one search per role, merged by profile).
    Per person: source_id, name, is_current (true / false / "unknown"), current_title,
    start_date, months_in_role, work_history, highlights.
    is_current "unknown" or false: don't list as current without a second source."""
    return exa.search_people(company, roles)


@_tool
def get_company(domain: str) -> dict:
    """Apollo firmographics: HQ, founded, industry, parent company, headcount + growth,
    department headcount, revenue, funding, AI tools in the tech stack.
    All values are Apollo's report: attribute them ("Apollo reports ...")."""
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


TOOLS = [search_web, fetch_page, search_people, get_company, get_jobs, verify_quote]


def build_server(auth_token: str | None = None) -> FastMCP:
    auth = None
    if auth_token:
        auth = DebugTokenVerifier(validate=lambda t: hmac.compare_digest(t, auth_token))
    return FastMCP("account-research", instructions=INSTRUCTIONS, tools=TOOLS, auth=auth)


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
        token = os.environ.get("MCP_AUTH_TOKEN")
        if not token:
            raise SystemExit("MCP_AUTH_TOKEN must be set for --http")
        build_server(token).run(transport="http", host=args.host, port=args.port)
    else:
        build_server().run()


if __name__ == "__main__":
    main()
