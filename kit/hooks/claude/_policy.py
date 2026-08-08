"""Shared policy loading for the agentkit Python hooks.

Every hook here fails CLOSED. Claude Code treats exit 1 as a non-blocking error and
proceeds with the action; only exit 2 blocks. So any condition that leaves the gate unable
to make an informed decision -- unreadable policy, malformed payload, an unparseable
pattern -- must exit 2 with an explanation on stderr, never exit 0.

The one deliberate exception is a repo with no policy at all: a repo that has not declared
any denials has nothing for these hooks to enforce, and refusing every call there would
make the kit unusable on first install. That case is distinguished by the FILE BEING
ABSENT, not by it being unreadable.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from fnmatch import fnmatch
from pathlib import Path

BLOCK = 2  # the only exit code Claude Code treats as blocking
ALLOW = 0


def fail_closed(message: str) -> None:
    """Refuse the call and say why. Never called for 'no policy declared'."""
    sys.stderr.write(f"agentkit gate: {message}\n")
    sys.exit(BLOCK)


def project_dir() -> Path:
    """Repo root. CLAUDE_PROJECT_DIR is set by Claude Code; cwd is the fallback."""
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def load_policy() -> dict:
    """Return the `policy` block of .agents/compatibility.json.

    Absent file  -> {} (nothing declared, nothing to enforce).
    Present file -> must parse, or the gate refuses. A repo that declares a policy and
                    then corrupts it is exactly the case where allowing through is wrong.
    """
    path = project_dir() / ".agents" / "compatibility.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail_closed(
            f".agents/compatibility.json exists but could not be read ({exc}). "
            "The gate cannot tell what is forbidden, so it refuses. Fix the file or delete it."
        )
    policy = data.get("policy") or {}
    if not isinstance(policy, dict):
        fail_closed(".agents/compatibility.json has a `policy` key that is not an object.")
    return policy


def read_payload() -> dict:
    """Parse the hook payload from stdin, or refuse."""
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail_closed(f"the hook payload could not be parsed as JSON ({exc}).")
    if not isinstance(payload, dict):
        fail_closed("the hook payload was not a JSON object.")
    return payload


def deny(reason: str) -> None:
    """Emit a structured deny. Exit 0 is correct here: the JSON carries the decision."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(ALLOW)


def add_context(text: str) -> None:
    """Allow, but inject context the agent must act on."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": text,
        }
    }))
    sys.exit(ALLOW)


def compile_pattern(pattern: str, where: str) -> "re.Pattern[str]":
    """Compile a policy regex, refusing rather than skipping on a bad one.

    A malformed pattern in a deny list is a hole: skip it and the rule silently stops
    protecting anything. Refusing is loud, and loud is the property that matters.
    """
    try:
        return re.compile(pattern)
    except re.error as exc:
        fail_closed(f"policy {where} contains an invalid regex {pattern!r} ({exc}).")


def path_matches(candidate: str, glob: str) -> bool:
    """Match a file path against a policy glob.

    Claude Code guarantees tool_input.file_path is absolute with ~ and relative spellings
    already expanded, so the repo-relative form is what policy globs are written against.
    Both forms are tried, and `**` is normalised, so `lib/content/live/**` matches a
    nested file the way an author would expect fnmatch to but it does not.
    """
    root = project_dir()
    try:
        rel = str(Path(candidate).resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        rel = candidate

    variants = {candidate, rel, "/" + rel.lstrip("/")}
    globs = {glob}
    if glob.endswith("/**"):
        globs.add(glob[:-3])           # the directory itself
        globs.add(glob[:-1] + "/*")    # one level, for fnmatch's flat semantics
    if "**/" in glob:
        globs.add(glob.replace("**/", ""))

    for value in variants:
        for pattern in globs:
            if fnmatch(value, pattern):
                return True
            # fnmatch treats * as crossing separators, which is what we want for `**`,
            # but a trailing `/**` must also match deeper paths.
            if pattern.endswith("/**") and value.startswith(pattern[:-3].rstrip("/") + "/"):
                return True
    return False


def script_lengths(text: str, scripts: list[str]) -> dict[str, int]:
    """Count characters per Unicode script.

    This exists so that a change to text the operator cannot read is still VISIBLE to
    them as a number. It is a measurement, not an assurance -- which is exactly why it
    survives the anti-verification-ritual rule: it is a gate on the world, not an
    instruction for the agent to double-check itself.
    """
    counts = {name: 0 for name in scripts}
    for ch in text:
        if not ch.strip():
            continue
        try:
            name = unicodedata.name(ch)
        except ValueError:
            continue
        for script in scripts:
            if name.startswith(script.upper() + " "):
                counts[script] += 1
                break
    return counts
