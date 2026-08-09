#!/usr/bin/env python3
"""PreToolUse gate for Edit|Write — path denials and before-measurements.

Two jobs, and they are different in kind.

DENIALS. `policy.denyWritePaths` names files no agent may edit. These are ALSO compiled
into settings.json `permissions.deny` by `agentkit apply`, deliberately twice: the
permission system is the client-enforced layer Anthropic points at for hard path blocks
("use the permission system rather than a hook to enforce a hard allow or deny"), and this
hook is what still fires when a settings layer is missing, excluded, or overridden. Two
independent layers, neither of which is the other's excuse.

BEFORE-MEASUREMENTS. `policy.measureOnWrite` names the observations that must accompany a
change -- string lengths in a script the operator does not read being the case this was built
for. Those exist because a human cannot read the output and needs a NUMBER to sanity-check
against. The retired design would have deleted them as "verification ritual"; they are not
ritual, they are gates on the world.

The important move is that this hook does not ASK the agent to take the measurement. It
takes it, and its PostToolUse sibling takes it again afterwards and reports the delta.
That converts a prose instruction followed roughly a third of the time into something that
happens whether or not the agent cooperates.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _policy import (  # noqa: E402
    ALLOW, add_context, deny, fail_closed, harness, load_policy, path_matches,
    read_payload, script_lengths, write_paths,
)

STASH = Path(tempfile.gettempdir()) / "agentkit-measure"


def stash_path(file_path: str) -> Path:
    digest = hashlib.sha256(file_path.encode("utf-8")).hexdigest()[:16]
    return STASH / f"{digest}.json"


def measure(path: Path, spec: dict) -> dict:
    """Take one measurement of a file as it currently stands on disk."""
    kind = spec.get("measure")
    if not path.exists():
        return {"kind": kind, "exists": False}
    try:
        if kind == "byte-size":
            return {"kind": kind, "exists": True, "bytes": path.stat().st_size}
        text = path.read_text(encoding="utf-8", errors="replace")
        if kind == "line-count":
            return {"kind": kind, "exists": True, "lines": len(text.splitlines())}
        if kind == "script-lengths":
            scripts = spec.get("scripts") or []
            return {"kind": kind, "exists": True, "scripts": script_lengths(text, scripts)}
    except OSError as exc:
        return {"kind": kind, "exists": True, "error": str(exc)}
    fail_closed(f"policy.measureOnWrite has an unknown measure {kind!r}.")


def main() -> None:
    payload = read_payload()
    paths = write_paths(payload)
    policy = load_policy()

    for file_path in paths:
        # Codex has no ConfigChange event. Protect the repo-local files that activate its
        # own hooks at the write-tool boundary; legitimate regeneration goes through
        # `agentkit apply`, whose Bash invocation is separately gated and reviewable.
        if harness() == "codex" and any(
            path_matches(file_path, protected)
            for protected in (".codex/hooks.json", ".codex/config.toml")
        ):
            deny(
                f"agentkit policy: Codex may not edit its own enforcement file {file_path} "
                "through apply_patch. Change the source kit and run `agentkit apply`."
            )

        for rule in policy.get("denyWritePaths") or []:
            if path_matches(file_path, rule["glob"]):
                deny(
                    f"agentkit policy: writing {file_path} is forbidden. {rule['reason']} "
                    f"(rule: denyWritePaths {rule['glob']} in .agents/compatibility.json)"
                )

    notes = []
    STASH.mkdir(parents=True, exist_ok=True)
    for file_path in paths:
        for spec in policy.get("measureOnWrite") or []:
            if not path_matches(file_path, spec["glob"]):
                continue
            before = measure(Path(file_path), spec)
            try:
                stash_path(file_path).write_text(json.dumps(before), encoding="utf-8")
            except OSError:
                # A missing stash only costs the delta line in PostToolUse; it is not a
                # reason to refuse an otherwise-permitted edit.
                pass
            notes.append(
                f"{file_path}: {spec['glob']} -> "
                f"{json.dumps(before.get('scripts', before))} ({spec['reason']})"
            )

    if notes:
        add_context(
            "[agentkit measurement — BEFORE] " + " | ".join(notes) +
            " . The after-measurement will be reported automatically once the edit lands; "
            "if a count moves in a direction you did not intend, stop and say so."
        )

    sys.exit(ALLOW)


if __name__ == "__main__":
    main()
