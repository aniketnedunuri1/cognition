import json

from claude_agent_sdk import ToolResultBlock

import agent


def test_tool_json_reads_mcp_text_content():
    block = ToolResultBlock(tool_use_id="t1", content=[{"type": "text", "text": json.dumps({"slack_summary": "Brex — Warm"})}])
    assert agent._tool_json(block) == {"slack_summary": "Brex — Warm"}
    assert agent._tool_json(ToolResultBlock(tool_use_id="t2", content="not json")) is None


def test_agent_only_gets_our_tools():
    names = agent.tool_names()
    assert names and all(n.startswith("mcp__research__") for n in names)
    assert "mcp__research__save_brief" in names
