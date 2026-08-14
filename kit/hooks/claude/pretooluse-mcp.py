#!/usr/bin/env python3
"""PreToolUse gate for MCP calls, which do not pass through the Bash hook.

Denials are declared in ``policy.denyMcpTools`` and can be narrowed by argument so allowed
uses of the same tool remain available.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _policy import (  # noqa: E402
    allow, compile_pattern, deny, fail_closed, harness, load_policy, read_payload,
)


def arg_matches(tool_input: dict, conditions: dict) -> bool:
    """True when EVERY declared argument condition matches.

    An absent argument does not match: a rule saying `target: ^production$` should not
        fire on a call that omits target entirely because the tool may define a safe default.
    """
    for name, pattern in conditions.items():
        value = tool_input.get(name)
        if value is None:
            return False
        if not compile_pattern(pattern, f"denyMcpTools.whenArgMatches.{name}").search(str(value)):
            return False
    return True


def main() -> None:
    payload = read_payload()
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input", {})
    wire = harness()

    if not isinstance(tool_name, str) or not tool_name:
        fail_closed("the MCP hook payload has no tool_name, so the gate cannot identify the call.")
    if wire == "cursor" and isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except json.JSONDecodeError as exc:
            fail_closed(f"the Cursor MCP hook tool_input is invalid JSON ({exc}).")
    if not isinstance(tool_input, dict):
        fail_closed("the MCP hook payload has no object-valued tool_input.")

    rules = load_policy().get("denyMcpTools") or []
    policy_name = tool_name
    if wire == "cursor":
        server_name = payload.get("mcp_server_name")
        if rules and (not isinstance(server_name, str) or not server_name):
            fail_closed(
                "the Cursor MCP hook payload has no mcp_server_name, so server-specific "
                "denyMcpTools rules cannot be evaluated."
            )
        if isinstance(server_name, str) and server_name:
            # Cursor exposes the server and bare tool separately. Rebuild the same logical
            # full-name shape RepoCharter policies use under Claude and Codex.
            safe_server = re.sub(r"[^A-Za-z0-9_.-]", "_", server_name)
            policy_name = f"mcp__{safe_server}__{tool_name}"

    for rule in rules:
        if not compile_pattern(rule["pattern"], "denyMcpTools.pattern").search(policy_name):
            continue
        conditions = rule.get("whenArgMatches") or {}
        if conditions and not arg_matches(tool_input, conditions):
            continue
        detail = f" (matched {conditions})" if conditions else ""
        deny(
            f"RepoCharter policy: {policy_name} is forbidden{detail}. {rule['reason']} "
            f"(rule: denyMcpTools {rule['pattern']} in .agents/compatibility.json)"
        )

    allow()


if __name__ == "__main__":
    main()
