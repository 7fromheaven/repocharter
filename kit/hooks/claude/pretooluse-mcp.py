#!/usr/bin/env python3
"""PreToolUse gate for MCP tools — the surface a Bash-only matcher cannot see.

The reference repo declared exactly one PreToolUse matcher, "Bash". That made every MCP
tool invisible to every safety rule in the repository, including a deploy tool whose
schema takes target: "production" and which is loadable in an ordinary session. No amount
of Bash regex reaches it, because no shell command is involved.

`mcp__.*` has been a supported matcher the entire time. This gate closes that hole.

Denials are declared in `policy.denyMcpTools`, each optionally narrowed by argument, so a
preview deploy stays available while a production deploy does not -- the goal is a gate
people keep, not one they disable the first time it blocks legitimate work.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _policy import ALLOW, compile_pattern, deny, load_policy, read_payload  # noqa: E402


def arg_matches(tool_input: dict, conditions: dict) -> bool:
    """True when EVERY declared argument condition matches.

    An absent argument does not match: a rule saying `target: ^production$` should not
    fire on a call that omits target entirely, because the tool's own default may well be
    the safe one. Denying what we cannot see would train people to remove the rule.
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
        sys.exit(ALLOW)

    for rule in load_policy().get("denyMcpTools") or []:
        if not compile_pattern(rule["pattern"], "denyMcpTools.pattern").search(tool_name):
            continue
        conditions = rule.get("whenArgMatches") or {}
        if conditions and not arg_matches(tool_input, conditions):
            continue
        detail = f" (matched {conditions})" if conditions else ""
        deny(
            f"agentkit policy: {tool_name} is forbidden{detail}. {rule['reason']} "
            f"(rule: denyMcpTools {rule['pattern']} in .agents/compatibility.json)"
        )

    sys.exit(ALLOW)


if __name__ == "__main__":
    main()
