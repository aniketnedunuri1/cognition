"""Local runner: the playbook as system prompt + the MCP server as the only tools.

    python agent.py research acme.com                 # stages 0-4 → brief, drafts, Slack summary
    python agent.py research acme.com --stage signals # one stage
    python agent.py feedback acme.com "Too long for the CTO; lead with the peer metric"
    python agent.py refresh-positioning
    python agent.py research acme.com --replay        # cache only: reproduce the committed demo

--server-url https://.../mcp shares state (cache, briefs, learned rules) with the
HTTP server Devin uses; otherwise the server runs locally over stdio.
Auth: ANTHROPIC_API_KEY, or an existing Claude Code login.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, ToolResultBlock,
                              ToolUseBlock, UserMessage, query)

ROOT = Path(__file__).resolve().parent
SERVER = "research"
MODEL = "claude-opus-5-5"
STAGES = ("company", "people", "signals", "draft")
RENDERERS = ("save_brief", "create_brief_doc")  # tools whose result carries the rendered Slack summary


def tool_names() -> list[str]:
    from server.app import TOOLS
    return [f"mcp__{SERVER}__{t.__name__}" for t in TOOLS]


def _tool_json(block: ToolResultBlock) -> dict | None:
    parts = block.content if isinstance(block.content, list) else [{"type": "text", "text": block.content or ""}]
    for part in parts:
        try:
            return json.loads(part.get("text") or "")
        except (json.JSONDecodeError, AttributeError):
            continue
    return None


def server_config(args) -> dict:
    if args.server_url:
        return {"type": "http", "url": args.server_url,
                "headers": {"Authorization": f"Bearer {os.environ['MCP_AUTH_TOKEN']}"}}
    # Launched from ROOT (options.cwd); the server loads .env itself.
    return {"type": "stdio", "command": sys.executable,
            "args": ["-m", "server.app"] + (["--replay"] if args.replay else [])}


def prompt_for(args) -> str:
    if args.command == "research":
        if args.stage:
            return (f"Account: {args.domain}. Run Stage 0, then only the `{args.stage}` stage of the playbook. "
                    f"Report the result to the rep; save_brief only if you produced a full brief.")
        return (f"Account: {args.domain}. Run Stages 0-4 of the playbook, then save_brief, fix what the "
                f"validator dropped with real evidence, save again, and call create_brief_doc.")
    if args.command == "feedback":
        return (f"Rep feedback on the {args.domain} brief:\n\n{args.text}\n\n"
                f"Run Stage 6 of the playbook (get_last_brief and get_playbook first).")
    return "Run the 'Maintaining positioning' procedure from the playbook until refresh_positioning returns no items."


async def run(args) -> int:
    options = ClaudeAgentOptions(
        system_prompt=(ROOT / "playbook" / "playbook.md").read_text(),
        mcp_servers={SERVER: server_config(args)},
        strict_mcp_config=True,         # only our server: no MCP servers / connectors from the user's account
        tools=[],                       # no built-in tools: every fact comes through the MCP server
        allowed_tools=tool_names(),
        permission_mode="dontAsk",      # anything not pre-approved is denied
        setting_sources=[],             # ignore local Claude Code settings / CLAUDE.md
        model=MODEL,
        effort="high",
        max_turns=args.max_turns,
        cwd=str(ROOT),
    )
    final, calls, summary, saved = None, {}, None, None
    async for msg in query(prompt=prompt_for(args), options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    calls[block.id] = block.name.removeprefix(f"mcp__{SERVER}__")
                    print(f"  → {calls[block.id]}", file=sys.stderr)
                elif isinstance(block, TextBlock) and args.verbose:
                    print(block.text, file=sys.stderr)
        elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, ToolResultBlock) and calls.get(block.tool_use_id) in RENDERERS:
                    data = _tool_json(block) or {}
                    summary = data.get("slack_summary") or summary
                    saved = data.get("saved") or saved
        elif isinstance(msg, ResultMessage):
            final = msg
    if final is None:
        print("No result returned.", file=sys.stderr)
        return 1
    if summary:
        # The rendered summary, verbatim from the tool. The agent's own closing prose is
        # unvalidated, so it's only shown on request (-v), labeled as such.
        print(summary)
        if saved:
            print(f"\nBrief: {saved} (+ .md)")
        if args.verbose and final.result:
            print(f"\n--- agent notes (not validated) ---\n{final.result}", file=sys.stderr)
    else:
        print(final.result or "")
    cost = f", ${final.total_cost_usd:.2f}" if final.total_cost_usd is not None else ""
    print(f"\n[{final.num_turns} turns, {final.duration_ms / 1000:.0f}s{cost}]", file=sys.stderr)
    return 1 if final.is_error else 0


def main() -> int:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description="Account research agent")
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("research")
    r.add_argument("domain")
    r.add_argument("--stage", choices=STAGES)
    f = sub.add_parser("feedback")
    f.add_argument("domain")
    f.add_argument("text")
    sub.add_parser("refresh-positioning")
    for p in (r, f, sub.choices["refresh-positioning"]):
        p.add_argument("--replay", action="store_true", help="cache only, no data API calls")
        p.add_argument("--server-url", help="shared HTTP MCP server (needs MCP_AUTH_TOKEN)")
        p.add_argument("--max-turns", type=int, default=150)
        p.add_argument("-v", "--verbose", action="store_true", help="print the agent's narration")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
