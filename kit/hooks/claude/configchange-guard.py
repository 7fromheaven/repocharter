#!/usr/bin/env python3
"""ConfigChange guard — refuses edits that disarm the gates.

Every control below Tier 0 fails open, and each fails open silently. The specific way this
system fails open is a settings edit: `disableAllHooks` is settable at project and local
scope with a live file watcher, so one write turns off every hook in this directory,
including the ones enforcing production fences.

This guard makes that edit loud. It cannot make it impossible -- a control the agent's own
uid can write is not security, and the file says so. Genuine immutability needs OS
ownership the agent cannot write: a managed-settings.json owned by root, which is Tier 0b
and needs one sudo.

Also refused: emptying a non-empty permissions.deny, and removing a kit-managed hook. Both
are the same move as disableAllHooks wearing different clothes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _policy import ALLOW, BLOCK, project_dir, read_payload  # noqa: E402

WATCHED = ("settings.json", "settings.local.json")


def refuse(reason: str) -> None:
    sys.stderr.write(f"agentkit ConfigChange guard: {reason}\n")
    sys.exit(BLOCK)


def load_current() -> dict:
    path = project_dir() / ".claude" / "settings.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> None:
    payload = read_payload()

    changed = str(payload.get("file_path") or payload.get("path") or "")
    if changed and not any(changed.endswith(name) for name in WATCHED):
        sys.exit(ALLOW)

    proposed = payload.get("new_settings") or payload.get("settings") or {}
    if not isinstance(proposed, dict):
        sys.exit(ALLOW)

    if proposed.get("disableAllHooks"):
        refuse(
            "refusing a settings change that sets disableAllHooks. That switch turns off "
            "every safety gate in this repository at once, including the production fences. "
            "If a specific hook is wrong, remove that hook and say why in the commit."
        )

    current = load_current()
    had_deny = (current.get("permissions") or {}).get("deny") or []
    now_deny = (proposed.get("permissions") or {}).get("deny") or []
    if had_deny and not now_deny:
        refuse(
            f"refusing a settings change that empties permissions.deny (was {len(had_deny)} "
            "rules). Those are the client-enforced path and tool blocks; emptying them is "
            "disableAllHooks by another route. Edit .agents/compatibility.json policy and "
            "run `agentkit apply` instead."
        )

    had_hooks = set((current.get("hooks") or {}).keys())
    now_hooks = set((proposed.get("hooks") or {}).keys())
    dropped = had_hooks - now_hooks
    if dropped:
        refuse(
            f"refusing a settings change that removes hook event(s): {', '.join(sorted(dropped))}. "
            "Re-add them, or change the policy and run `agentkit apply` so the removal is "
            "recorded rather than silent."
        )

    sys.exit(ALLOW)


if __name__ == "__main__":
    main()
