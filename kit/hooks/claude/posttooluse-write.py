#!/usr/bin/env python3
"""PostToolUse sibling of pretooluse-write.py — the after-measurement and the delta.

This is the half that makes the measurement real. A prose rule saying "report HY string
lengths before and after any change near bilingual content" is a Tier-3 control: measured
strict compliance with long-standing instructions runs around 36% at best configuration,
so roughly two times in three it simply does not happen, and nobody finds out.

Taking the measurement here instead means it happens on every matching edit regardless of
what the agent decides to do -- which is the difference Anthropic draws between context
and enforced configuration.

This hook never blocks. A PostToolUse denial cannot un-write the file, and pretending
otherwise would be theatre. Its job is to make the change VISIBLE, in a number, to an
operator who cannot read the content.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _policy import ALLOW, load_policy, path_matches, read_payload, script_lengths  # noqa: E402

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
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not file_path:
        sys.exit(ALLOW)

    policy = load_policy()
    lines = []
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
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    "[agentkit measurement — AFTER] " + " | ".join(lines) +
                    " . Report these numbers to the operator with the change. A count that "
                    "moved unexpectedly in text you cannot read is the signal this gate exists for."
                ),
            }
        }))
    sys.exit(ALLOW)


if __name__ == "__main__":
    main()
