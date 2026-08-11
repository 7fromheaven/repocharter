#!/usr/bin/env python3
"""PreToolUse gate for MCP calls, which do not pass through the Bash hook.

Denials are declared in ``policy.denyMcpTools`` and can be narrowed by argument so allowed
uses of the same tool remain available.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _policy import (  # noqa: E402
    ALLOW, compile_pattern, deny, fail_closed, load_policy, read_payload,
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
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}

    if not tool_name:
        fail_closed("the MCP hook payload has no tool_name, so the gate cannot identify the call.")
    if not isinstance(tool_input, dict):
        fail_closed("the MCP hook payload has no object-valued tool_input.")

    for rule in load_policy().get("denyMcpTools") or []:
        if not compile_pattern(rule["pattern"], "denyMcpTools.pattern").search(tool_name):
            continue
        conditions = rule.get("whenArgMatches") or {}
        if conditions and not arg_matches(tool_input, conditions):
            continue
        detail = f" (matched {conditions})" if conditions else ""
        deny(
            f"RepoCharter policy: {tool_name} is forbidden{detail}. {rule['reason']} "
            f"(rule: denyMcpTools {rule['pattern']} in .agents/compatibility.json)"
        )

    sys.exit(ALLOW)


if __name__ == "__main__":
    main()
