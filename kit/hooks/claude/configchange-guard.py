#!/usr/bin/env python3
"""ConfigChange guard — refuses settings reloads that disarm the gates.

Every control below Tier 0 fails open, and each fails open silently. The specific way this
system fails open is a settings edit: `disableAllHooks` is settable at project and local
scope with a live file watcher, so one write turns off every hook in this directory,
including the ones enforcing production fences.

This guard makes that edit loud. It cannot make it impossible -- a control the agent's own
uid can write is not security, and the file says so. Genuine immutability needs OS
ownership the agent cannot write: a managed-settings.json owned by root, which is Tier 0b
and needs one sudo.

Also refused: dropping or replacing any kit-managed handler mapping in project settings, and
emptying permissions.deny while the declaration still names path or MCP denials. Both are the
same move as disableAllHooks wearing different clothes.

WHERE THE PROPOSED SETTINGS COME FROM. The harness fires this event from a file watcher and
sends only `{hook_event_name, source, file_path}` -- the changed file's CONTENT is not in
the payload. A guard that waits for a `new_settings` key therefore never inspects anything,
and every branch below it becomes unreachable. The content is read from `file_path` instead,
and a payload that does carry settings is still honoured so a harness that supplies them
needs no second code path.

WHY THE BASELINE IS THE DECLARATION, NOT THE PREVIOUS FILE. The watcher fires AFTER the
write lands, so the on-disk copy of a changed settings.json is already the new content and
cannot serve as a before-image. The kit-managed handler wiring and the repo's declared policy
are what the proposed settings are checked against; both are fixed points that do not
depend on catching the file mid-change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _policy import ALLOW, BLOCK, project_dir, read_payload  # noqa: E402

WATCHED = ("settings.json", "settings.local.json")

# The exact hook wiring `agentkit apply` installs. Checking event names alone is decorative:
# replacing a real handler with ``true`` leaves every event present while enforcing nothing.
HOOK_PREFIX = "${CLAUDE_PROJECT_DIR}/.claude/hooks/agentkit"
REQUIRED_HOOK_WIRING = (
    ("PreToolUse", "Bash", f"{HOOK_PREFIX}/pretooluse-bash.sh"),
    ("PreToolUse", "Edit|Write|NotebookEdit", f"{HOOK_PREFIX}/pretooluse-write.py"),
    ("PreToolUse", "mcp__.*", f"{HOOK_PREFIX}/pretooluse-mcp.py"),
    ("PostToolUse", "Edit|Write|NotebookEdit", f"{HOOK_PREFIX}/posttooluse-write.py"),
    ("ConfigChange", None, f"{HOOK_PREFIX}/configchange-guard.py"),
)


def refuse(reason: str) -> None:
    sys.stderr.write(f"agentkit ConfigChange guard: {reason}\n")
    sys.exit(BLOCK)


def declared_policy() -> dict:
    path = project_dir() / ".agents" / "compatibility.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("policy") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def proposed_settings(payload: dict, changed: Path | None) -> dict:
    """The settings that would take effect if this change were loaded."""
    for key in ("new_settings", "settings"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value

    if changed is None:
        refuse(
            "the ConfigChange payload names neither a settings file nor its contents, so "
            "the guard cannot tell what would be loaded and refuses the reload."
        )
    if not changed.exists():
        return {}  # deletion: judged on what it leaves behind, same as any other content
    try:
        loaded = json.loads(changed.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        refuse(
            f"{changed} changed but could not be read as JSON ({exc}). The guard cannot tell "
            "whether the new settings keep the gates installed, so it refuses the reload."
        )
    if not isinstance(loaded, dict):
        refuse(f"{changed} does not contain a JSON object, so no settings can be read from it.")
    return loaded


def missing_hook_wiring(hooks: dict) -> list[str]:
    missing: list[str] = []
    for event, matcher, command in REQUIRED_HOOK_WIRING:
        groups = hooks.get(event)
        groups = groups if isinstance(groups, list) else []
        matching = [group for group in groups if isinstance(group, dict)
                    and group.get("matcher") == matcher]
        handlers = [handler for group in matching for handler in (group.get("hooks") or [])
                    if isinstance(handler, dict)]
        wired = any(handler.get("type") == "command"
                    and handler.get("command") == command for handler in handlers)
        if not wired:
            missing.append(f"{event} matcher {matcher or '(none)'} -> {Path(command).name}")
    return missing


def main() -> None:
    payload = read_payload()

    raw = str(payload.get("file_path") or payload.get("path") or "")
    if raw and not any(raw.endswith(name) for name in WATCHED):
        sys.exit(ALLOW)
    changed = Path(raw).expanduser() if raw else None
    if changed is not None and not changed.is_absolute():
        changed = project_dir() / changed

    proposed = proposed_settings(payload, changed)

    if proposed.get("disableAllHooks"):
        refuse(
            "refusing a settings change that sets disableAllHooks. That switch turns off "
            "every safety gate in this repository at once, including the production fences. "
            "If a specific hook is wrong, remove that hook and say why in the commit."
        )

    # The remaining two checks describe the PROJECT settings file. User- and
    # enterprise-scope settings legitimately carry neither this repo's hooks nor its
    # denials, and settings.local.json merges on top rather than replacing, so demanding
    # either of them there would refuse ordinary machine-local configuration.
    project_settings = (project_dir() / ".claude" / "settings.json").resolve()
    try:
        is_project_settings = changed is not None and changed.resolve() == project_settings
    except OSError:
        is_project_settings = False
    if not is_project_settings:
        sys.exit(ALLOW)

    hooks = proposed.get("hooks")
    hooks = hooks if isinstance(hooks, dict) else {}
    missing = missing_hook_wiring(hooks)
    if missing:
        refuse(
            f"refusing a settings change that leaves required project hook wiring absent: "
            f"{'; '.join(missing)}. Re-add it, or change the policy and run `agentkit "
            "apply` so the removal is recorded rather than silent."
        )

    policy = declared_policy()
    declares_denials = bool(policy.get("denyWritePaths") or policy.get("denyMcpTools"))
    now_deny = (proposed.get("permissions") or {}).get("deny") or []
    if declares_denials and not now_deny:
        refuse(
            "refusing a settings change that empties permissions.deny while "
            ".agents/compatibility.json still declares path or MCP denials. Those are the "
            "client-enforced blocks; emptying them is disableAllHooks by another route. "
            "Edit the declaration and run `agentkit apply` instead."
        )

    sys.exit(ALLOW)


if __name__ == "__main__":
    main()
