#!/usr/bin/env python3
"""PostToolUse sibling of pretooluse-write.py.

For each matching edit, this hook records the configured after-measurement and reports its
delta from the pre-write value. It never blocks because the write has already occurred.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _policy import (  # noqa: E402
    ALLOW, harness, load_policy, path_matches, read_payload, script_lengths, write_paths,
)

STASH = Path(tempfile.gettempdir()) / "agentkit-measure"


def stash_path(file_path: str) -> Path:
    digest = hashlib.sha256(file_path.encode("utf-8")).hexdigest()[:16]
    return STASH / f"{digest}.json"


def measure(path: Path, spec: dict) -> dict:
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
            return {"kind": kind, "exists": True,
                    "scripts": script_lengths(text, spec.get("scripts") or [])}
    except OSError as exc:
        return {"kind": kind, "exists": True, "error": str(exc)}
    return {"kind": kind, "unknown": True}


def describe_delta(before: dict, after: dict) -> str:
    """Render the change as a signed number per measured dimension."""
    if not before or before.get("exists") is False:
        return "no before-measurement (new file)"
    parts = []
    for key in ("bytes", "lines"):
        if key in before and key in after:
            change = after[key] - before[key]
            parts.append(f"{key} {before[key]} -> {after[key]} ({change:+d})")
    b_scripts, a_scripts = before.get("scripts") or {}, after.get("scripts") or {}
    for script in sorted(set(b_scripts) | set(a_scripts)):
        b, a = b_scripts.get(script, 0), a_scripts.get(script, 0)
        marker = "" if b == a else "  <-- CHANGED"
        parts.append(f"{script} {b} -> {a} ({a - b:+d}){marker}")
    return ", ".join(parts) if parts else "no comparable dimensions"


def main() -> None:
    payload = read_payload()
    paths = write_paths(payload)
    policy = load_policy()
    lines = []
    for file_path in paths:
        for spec in policy.get("measureOnWrite") or []:
            if not path_matches(file_path, spec["glob"]):
                continue
            after = measure(Path(file_path), spec)
            before = {}
            stash = stash_path(file_path)
            try:
                if stash.exists():
                    before = json.loads(stash.read_text(encoding="utf-8"))
                    stash.unlink()
            except (OSError, json.JSONDecodeError):
                before = {}
            lines.append(f"{Path(file_path).name}: {describe_delta(before, after)}")

    if lines:
        context = (
            "[RepoCharter measurement — AFTER] " + " | ".join(lines) +
            " . Report these numbers with the change and review unexpected deltas."
        )
        if harness() == "cursor":
            print(json.dumps({"additional_context": context}))
        else:
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": context,
                }
            }))
    elif harness() == "cursor":
        print("{}")
    sys.exit(ALLOW)


if __name__ == "__main__":
    main()
