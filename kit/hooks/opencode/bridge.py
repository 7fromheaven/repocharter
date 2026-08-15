#!/usr/bin/env python3
"""Translate OpenCode plugin calls into RepoCharter's shared hook protocol.

The user-level JavaScript plugin is deliberately policy-free.  This bridge lives in each
repository's vendored kit, normalizes OpenCode's tool arguments, and executes the same
fail-closed Bash/write/MCP gates used by Claude Code, Codex, and Cursor.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HOOKS = ROOT / ".claude" / "hooks" / "agentkit"
WRITE_TOOLS = {"write", "edit", "apply_patch", "patch"}
PROTECTED_WRITES = (
    ".agents/compatibility.json",
    ".claude/hooks/agentkit/",
    "kit/hooks/opencode/",
)


def emit(decision: str, reason: str = "", context: str = "") -> None:
    print(json.dumps({
        "decision": decision,
        "reason": reason,
        "additional_context": context,
    }))


def refuse(reason: str) -> None:
    emit("deny", f"RepoCharter OpenCode adapter: {reason}")
    raise SystemExit(0)


def read_call() -> dict:
    try:
        value = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        refuse(f"plugin payload is not valid JSON ({exc})")
    if not isinstance(value, dict):
        refuse("plugin payload is not an object")
    project = value.get("projectDir")
    if not isinstance(project, str) or not project:
        refuse("plugin payload has no projectDir")
    try:
        supplied = Path(project).resolve()
    except OSError as exc:
        refuse(f"projectDir cannot be resolved ({exc})")
    if supplied != ROOT:
        refuse(f"plugin projectDir {supplied} does not match vendored kit root {ROOT}")
    return value


def repo_relative(candidate: str) -> str:
    try:
        return str(Path(candidate).resolve().relative_to(ROOT))
    except (ValueError, OSError):
        return candidate[2:] if candidate.startswith("./") else candidate


def protects(candidate: str) -> bool:
    relative = repo_relative(candidate)
    return any(
        relative == protected.rstrip("/") or relative.startswith(protected)
        for protected in PROTECTED_WRITES
    )


def protected_write(args: dict, tool: str) -> str | None:
    if tool in {"write", "edit"}:
        candidate = args.get("filePath")
        return candidate if isinstance(candidate, str) and protects(candidate) else None
    if tool in {"apply_patch", "patch"}:
        patch = args.get("patchText")
        if not isinstance(patch, str):
            return None
        for line in patch.splitlines():
            for prefix in ("*** Add File:", "*** Update File:", "*** Delete File:", "*** Move to:"):
                if line.startswith(prefix):
                    candidate = line[len(prefix):].strip()
                    if protects(candidate):
                        return candidate
    return None


def hook_payload(tool: str, args: dict, canonical_tool: str | None) -> tuple[Path | None, dict]:
    if tool == "bash":
        command = args.get("command")
        if not isinstance(command, str) or not command:
            refuse("Bash call has no string command")
        return HOOKS / "pretooluse-bash.sh", {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "command": command,
            "permission_mode": "bypassPermissions",
        }

    if tool in {"write", "edit"}:
        path = args.get("filePath")
        if not isinstance(path, str) or not path:
            refuse(f"{tool} call has no string filePath")
        return HOOKS / "pretooluse-write.py", {
            "tool_name": tool,
            "tool_input": {"file_path": path},
        }

    if tool in {"apply_patch", "patch"}:
        patch = args.get("patchText")
        if not isinstance(patch, str) or not patch:
            refuse(f"{tool} call has no string patchText")
        return HOOKS / "pretooluse-write.py", {
            "tool_name": tool,
            "tool_input": {"command": patch},
        }

    if canonical_tool is not None:
        if not isinstance(canonical_tool, str) or not canonical_tool.startswith("mcp__"):
            refuse("MCP call has an invalid canonical identity")
        return HOOKS / "pretooluse-mcp.py", {
            "tool_name": canonical_tool,
            "tool_input": args,
        }

    return None, {}


def post_payload(tool: str, args: dict) -> tuple[Path | None, dict]:
    if tool in {"write", "edit"}:
        path = args.get("filePath")
        if not isinstance(path, str) or not path:
            refuse(f"post-{tool} call has no string filePath")
        return HOOKS / "posttooluse-write.py", {
            "tool_name": tool,
            "tool_input": {"file_path": path},
        }
    if tool in {"apply_patch", "patch"}:
        patch = args.get("patchText")
        if not isinstance(patch, str) or not patch:
            refuse(f"post-{tool} call has no string patchText")
        return HOOKS / "posttooluse-write.py", {
            "tool_name": tool,
            "tool_input": {"command": patch},
        }
    return None, {}


def execute(hook: Path, payload: dict) -> tuple[str, str, str]:
    if not hook.is_file():
        return "deny", f"required gate is missing: {hook}", ""
    command = ["bash", str(hook)] if hook.suffix == ".sh" else [sys.executable, str(hook)]
    env = dict(os.environ)
    if hook.name == "pretooluse-mcp.py":
        env.update({
            "AGENTKIT_HARNESS": "claude-code",
            "CLAUDE_PROJECT_DIR": str(ROOT),
        })
    else:
        # Cursor's neutral JSON wire has explicit allow/ask/deny results. The bridge maps
        # ask to a denial because OpenCode cannot request native approval from this hook.
        env.update({
            "AGENTKIT_HARNESS": "cursor",
            "CURSOR_PROJECT_DIR": str(ROOT),
        })
    try:
        proc = subprocess.run(
            command,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=env,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "deny", f"gate could not run ({exc})", ""
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        return "deny", detail, ""
    if not proc.stdout.strip():
        return "allow", "", ""
    try:
        output = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return "deny", f"gate returned malformed JSON ({exc})", ""
    if not isinstance(output, dict):
        return "deny", "gate returned a non-object decision", ""
    decision = output.get("permission")
    reason = output.get("agent_message") or output.get("user_message") or ""
    context = output.get("additional_context") or ""
    if decision is None and context:
        decision = "allow"
    if decision not in {"allow", "deny"}:
        specific = output.get("hookSpecificOutput")
        if isinstance(specific, dict):
            decision = specific.get("permissionDecision") or "allow"
            reason = specific.get("permissionDecisionReason") or reason
            context = specific.get("additionalContext") or context
    if decision == "ask":
        return "deny", (
            f"{reason} OpenCode has no dependable plugin-to-native approval handoff; "
            "RepoCharter fails closed."
        ), context
    if decision not in {"allow", "deny"}:
        return "deny", f"gate returned unknown decision {decision!r}", context
    return decision, str(reason), str(context)


def main() -> None:
    call = read_call()
    phase = call.get("phase")
    tool = call.get("tool")
    args = call.get("args")
    if phase not in {"before", "after"}:
        refuse(f"unknown phase {phase!r}")
    if not isinstance(tool, str) or not tool:
        refuse("plugin payload has no tool name")
    if not isinstance(args, dict):
        refuse("plugin payload args are not an object")

    if phase == "after":
        hook, payload = post_payload(tool, args)
    else:
        protected = protected_write(args, tool)
        if protected:
            refuse(
                f"OpenCode may not edit its RepoCharter enforcement file {protected} "
                "through an agent write; change the source kit and run `repocharter apply`"
            )
        canonical = call.get("canonicalTool")
        if canonical is not None and not isinstance(canonical, str):
            refuse("canonicalTool is not a string")
        hook, payload = hook_payload(tool, args, canonical)
    if hook is None:
        emit("allow")
        return
    decision, reason, context = execute(hook, payload)
    emit(decision, reason, context)


if __name__ == "__main__":
    main()
