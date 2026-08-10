#!/usr/bin/env python3
"""Offline conformance checks that require repository-specific declarations.

agnix carries a broad sourced catalogue and remains available through `verify --agnix`.
The default commit path cannot fetch dependencies, so this checked-in module owns the
load-bearing offline checks directly.

This module covers checks absent from the shared catalogue and checks that must read
``.agents/compatibility.json``:

  1. Symlink conformance for the .claude/skills adapter, including directories created by
     Claude Code itself.
  2. The declaration matching the filesystem, including autoMemory against settings.json.
  3. Dead references across settings.json permissions.allow as well as hook blocks.
  4. A repo .codex/config.toml lowering project_doc_max_bytes or declaring fallback
     filenames, either of which can truncate or replace repository instructions.
  5. claudeMdExcludes, the same amputation shape on the Claude side.

Exit codes: 0 clean or warnings only, 1 errors found, 2 the checker could not run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

SPEC_SKILL_FIELDS = {
    "allowed-tools", "compatibility", "description", "license", "metadata", "name",
}
# Fields Claude Code accepts locally but which break the claude.ai upload path. Present =
# warn, never fail: the skill still loads where it is used.
#
# From the Claude Code skills documentation. These fields are valid locally but may be
# incompatible with the claude.ai upload path, so they produce warnings rather than errors.
CLAUDE_ONLY_SKILL_FIELDS = {
    "paths", "disable-model-invocation", "user-invocable", "argument-hint", "arguments",
    "disallowed-tools", "model", "effort", "context", "agent", "background", "hooks",
}

RETIRED_LEGACY_SKILLS = {
    "checkpoint", "handoff", "lessons", "plan-sync", "state-router",
    "dispatch-gate", "frontmatter-lint", "id-spine",
}

# Paths Claude Code creates under .claude/skills. These defaults are tolerated without a
# repository declaration; compatibility.json `harnessWritten` can extend but not shrink the
# set.
DEFAULT_HARNESS_WRITTEN = (".claude/skills/verify", ".claude/skills/run-*")

CODEX_ADAPTER_FILES = (
    ".codex/hooks.json",
    ".claude/hooks/agentkit/_policy.py",
    ".claude/hooks/agentkit/pretooluse-bash.sh",
    ".claude/hooks/agentkit/pretooluse-write.py",
    ".claude/hooks/agentkit/posttooluse-write.py",
    ".claude/hooks/agentkit/pretooluse-mcp.py",
)

# Kept byte-identical to the tuple in `agentkit`: the digest recorded by self-test and the
# digest recomputed by verify have to cover the same wire path or the check is decorative.
CLAUDE_ADAPTER_FILES = (
    ".claude/settings.json",
    ".claude/hooks/agentkit/_policy.py",
    ".claude/hooks/agentkit/pretooluse-bash.sh",
    ".claude/hooks/agentkit/pretooluse-write.py",
    ".claude/hooks/agentkit/posttooluse-write.py",
    ".claude/hooks/agentkit/pretooluse-mcp.py",
    ".claude/hooks/agentkit/configchange-guard.py",
)

# (event, matcher, script) triples `agentkit apply` installs into .claude/settings.json.
# A `blocking` Claude claim asserts all five are on the wire.
CLAUDE_HOOK_WIRING = (
    ("PreToolUse", "Bash", "pretooluse-bash.sh"),
    ("PreToolUse", "Edit|Write|NotebookEdit", "pretooluse-write.py"),
    ("PreToolUse", "mcp__.*", "pretooluse-mcp.py"),
    ("PostToolUse", "Edit|Write|NotebookEdit", "posttooluse-write.py"),
    ("ConfigChange", None, "configchange-guard.py"),
)

CHECKOUT_EVIDENCE_VERSION = 1

PRECOMMIT_BEGIN = "# >>> agentkit >>>"
PRECOMMIT_END = "# <<< agentkit <<<"


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    todos: list[str] = field(default_factory=list)
    stage: str = "migrated"

    def error(self, msg: str) -> None:
        """Wrong at every stage. A dead reference is a dead reference on day one."""
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def stage_error(self, msg: str) -> None:
        """Expected until the migration finishes; an error once the repo claims to be done.

        This keeps incomplete migration work distinct from regressions in migrated repos.
        """
        if self.stage == "migrated":
            self.errors.append(msg)
        else:
            self.todos.append(msg)


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _schema_type_ok(value, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return True


def _resolve_schema_ref(schema_root: dict, ref: str) -> dict | None:
    if not ref.startswith("#/"):
        return None
    node = schema_root
    for raw in ref[2:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node if isinstance(node, dict) else None


def _schema_errors(value, schema: dict, schema_root: dict, path: str = "$", depth: int = 0) -> list[str]:
    """Validate the dependency-free subset used by compatibility.schema.json.

    Pulling a JSON Schema package into a tool that must run on a fresh clone would break the
    zero-dependency contract. This deliberately implements only the checked-in schema's
    vocabulary; an unsupported keyword remains documentation rather than being guessed at.
    """
    if depth > 40:
        return [f"{path}: schema nesting exceeds 40 levels"]
    if "$ref" in schema:
        target = _resolve_schema_ref(schema_root, schema["$ref"])
        if target is None:
            return [f"{path}: unresolved schema reference {schema['$ref']!r}"]
        return _schema_errors(value, target, schema_root, path, depth + 1)

    errors: list[str] = []
    expected = schema.get("type")
    if expected and not _schema_type_ok(value, expected):
        return [f"{path}: expected {expected}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']!r}")

    if isinstance(value, dict):
        for key in schema.get("required") or []:
            if key not in value:
                errors.append(f"{path}: missing required key {key!r}")
        properties = schema.get("properties") or {}
        additional = schema.get("additionalProperties", True)
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in properties:
                errors.extend(_schema_errors(child, properties[key], schema_root,
                                             child_path, depth + 1))
            elif additional is False:
                errors.append(f"{child_path}: unknown key")
            elif isinstance(additional, dict):
                errors.extend(_schema_errors(child, additional, schema_root,
                                             child_path, depth + 1))
    elif isinstance(value, list):
        minimum = schema.get("minItems")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path}: needs at least {minimum} item(s)")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_schema_errors(item, item_schema, schema_root,
                                             f"{path}[{index}]", depth + 1))
    elif isinstance(value, str):
        minimum = schema.get("minLength")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path}: must be at least {minimum} character(s)")
        pattern = schema.get("pattern")
        if pattern and not re.search(pattern, value):
            errors.append(f"{path}: {value!r} does not match {pattern!r}")
        if schema.get("format") == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            errors.append(f"{path}: {value!r} is not an ISO date")
    elif isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} is below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} is above {schema['maximum']}")

    if "not" in schema and not _schema_errors(value, schema["not"], schema_root,
                                                path, depth + 1):
        errors.append(f"{path}: matches a forbidden schema shape")
    if "oneOf" in schema:
        matches = sum(not _schema_errors(value, branch, schema_root, path, depth + 1)
                      for branch in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: must match exactly one of {len(schema['oneOf'])} shapes")
    return errors


def check_schema(root: Path, compat: dict, rep: Report) -> None:
    """Validate the declaration against the checked-in schema, offline."""
    bundled = Path(__file__).resolve().parent.parent / "schema" / "compatibility.schema.json"
    declared = compat.get("$schema")
    schema_path = bundled
    if isinstance(declared, str) and "://" not in declared:
        candidate = (root / ".agents" / declared).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            rep.error(f"compatibility.json $schema resolves outside the repo: {declared}")
            return
        if not candidate.exists():
            rep.error(f"compatibility.json $schema does not exist: {declared}")
            return
        schema_path = candidate
    schema = read_json(schema_path)
    if not isinstance(schema, dict):
        rep.error(f"compatibility schema is unreadable or invalid JSON: {schema_path}")
        return
    for problem in _schema_errors(compat, schema, schema)[:40]:
        rep.error(f"compatibility schema: {problem}")


def _version_at_least(compat: dict, wanted: tuple[int, int, int]) -> bool:
    try:
        actual = tuple(int(part) for part in compat.get("agentkitVersion", "0.0.0").split("."))
    except (AttributeError, ValueError):
        return False
    return actual >= wanted


def adapter_digest(root: Path, files: tuple[str, ...]) -> str | None:
    digest = hashlib.sha256()
    for rel in files:
        path = root / rel
        if not path.is_file():
            return None
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def codex_adapter_digest(root: Path) -> str | None:
    return adapter_digest(root, CODEX_ADAPTER_FILES)


def claude_adapter_digest(root: Path) -> str | None:
    return adapter_digest(root, CLAUDE_ADAPTER_FILES)


def checkout_git_dir(root: Path) -> Path | None:
    """Return this worktree's private Git directory, not a shared common directory."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--absolute-git-dir"], cwd=root,
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    path = proc.stdout.strip()
    return Path(path) if proc.returncode == 0 and path else None


def checkout_evidence_path(root: Path, harness: str) -> Path | None:
    git_dir = checkout_git_dir(root)
    return (git_dir / "agentkit" / "enforcement" / f"{harness}.json") \
        if git_dir is not None else None


def check_checkout_evidence(root: Path, harness: str, evidence: dict,
                            compat: dict, rep: Report) -> None:
    """Require machine-local proof when the corresponding provider CLI is installed."""
    path = checkout_evidence_path(root, harness)
    local = read_json(path) if path is not None else None
    if not isinstance(local, dict):
        rep.error(
            f"{harness} is declared blocking but this checkout has no matching local "
            f"attestation. Run the live self-test in this checkout."
        )
        return
    expected = {
        "formatVersion": CHECKOUT_EVIDENCE_VERSION,
        "agentkitVersion": compat.get("agentkitVersion"),
        "checkout": str(root.resolve()),
        "provider": harness,
        "evidence": evidence,
    }
    if local != expected:
        rep.error(
            f"{harness} checkout-local attestation is stale or belongs to another checkout. "
            f"Re-run the live self-test here."
        )


def claude_config_dir() -> Path:
    raw = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".claude"


def claude_state_path() -> Path:
    config_dir = claude_config_dir()
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        candidates = (Path(f"{config_dir}.json"), config_dir / ".claude.json")
        return next((path for path in candidates if path.is_file()), candidates[0])
    return Path.home() / ".claude.json"


def claude_trust_root(root: Path) -> Path | None:
    state = read_json(claude_state_path()) or {}
    projects = state.get("projects")
    if not isinstance(projects, dict):
        return None
    target = root.resolve()
    trusted: list[Path] = []
    for raw, project in projects.items():
        if not isinstance(raw, str) or not isinstance(project, dict) \
                or project.get("hasTrustDialogAccepted") is not True:
            continue
        candidate = Path(raw).expanduser().resolve()
        try:
            target.relative_to(candidate)
        except (ValueError, OSError):
            continue
        trusted.append(candidate)
    return max(trusted, key=lambda path: len(path.parts), default=None)


def claude_local_settings_paths(root: Path) -> list[Path]:
    paths = [root / ".claude" / "settings.local.json"]
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"], cwd=root,
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        proc = None
    if proc is not None and proc.returncode == 0:
        first = next((line.removeprefix("worktree ") for line in proc.stdout.splitlines()
                      if line.startswith("worktree ")), "")
        if first:
            paths.append(Path(first) / ".claude" / "settings.local.json")
    unique: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def frontmatter(text: str) -> dict | None:
    """Parse the YAML-ish frontmatter block without a YAML dependency.

    Only flat `key: value` pairs and simple `- item` lists are understood, which is all the
    skill and rules specs use. Anything more complex is reported as unparseable rather than
    guessed at, because guessing here would silently pass a malformed file.
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    block, out, current = text[3:end], {}, None
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith(("  - ", "- ", "\t- ")):
            if current:
                out.setdefault(current, []).append(line.split("- ", 1)[1].strip())
            continue
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            current = key
            out[key] = value if value else []
    return out


# ── check 1: skills adapter ───────────────────────────────────────────────────────────

def check_skills(root: Path, compat: dict, rep: Report) -> None:
    canonical = root / (compat.get("canonical", {}).get("skills") or ".agents/skills")
    claude_dir = root / ".claude" / "skills"
    harness_written = list(DEFAULT_HARNESS_WRITTEN) + list(compat.get("harnessWritten") or [])

    if not canonical.is_dir():
        rep.note(f"no canonical skills directory at {canonical.relative_to(root)}; skipping skill checks")
        return

    skills = [p for p in canonical.iterdir() if p.is_dir()]
    warn_at = (compat.get("budgets") or {}).get("skillWarnCount", 20)
    if len(skills) > warn_at:
        rep.warn(
            f"{len(skills)} project skills, above the warning threshold of {warn_at}. "
            "Codex shortens descriptions before dropping skills, so the cost of overshoot "
            "is description fidelity. The signal worth acting on is description collision, "
            "not the count itself."
        )

    for skill in sorted(skills):
        spec = skill / "SKILL.md"
        if not spec.exists():
            rep.error(f"{spec.relative_to(root)} is missing (a skill directory must contain SKILL.md)")
            continue
        meta = frontmatter(spec.read_text(encoding="utf-8", errors="replace"))
        if meta is None:
            rep.error(f"{spec.relative_to(root)} has no parseable frontmatter block")
            continue
        if "description" not in meta:
            rep.error(f"{spec.relative_to(root)} is missing `description`, the one field every harness requires")
        if "name" not in meta:
            rep.error(f"{spec.relative_to(root)} is missing `name`, which Codex requires")
        unknown = set(meta) - SPEC_SKILL_FIELDS - CLAUDE_ONLY_SKILL_FIELDS
        if unknown:
            rep.error(
                f"{spec.relative_to(root)} has frontmatter fields outside the six-field spec set: "
                f"{', '.join(sorted(unknown))}"
            )
        claude_only = set(meta) & CLAUDE_ONLY_SKILL_FIELDS
        if claude_only:
            rep.warn(
                f"{spec.relative_to(root)} uses Claude-only frontmatter ({', '.join(sorted(claude_only))}). "
                "It loads locally; it will be rejected by claude.ai upload and the Skills API."
            )

    if not claude_dir.is_dir():
        if skills:
            rep.error(
                ".claude/skills does not exist, so Claude Code cannot see any project skill. "
                "Run `agentkit apply` to create the relative symlinks."
            )
        return

    for entry in sorted(claude_dir.iterdir()):
        name = entry.name
        if name.startswith("."):
            continue
        if name in RETIRED_LEGACY_SKILLS:
            rep.stage_error(
                f".claude/skills/{name} is a retired legacy-layout skill. Delete it; its procedure "
                "belongs in .agents/skills or nowhere."
            )
            continue
        if entry.is_symlink():
            target = os.readlink(entry)
            if os.path.isabs(target):
                rep.error(
                    f".claude/skills/{name} is an ABSOLUTE symlink ({target}). It will break on "
                    "any other machine or a fresh clone. Use a relative link."
                )
                continue
            resolved = (entry.parent / target).resolve()
            try:
                resolved.relative_to(canonical.resolve())
            except ValueError:
                rep.error(
                    f".claude/skills/{name} resolves to {resolved}, outside the canonical skills "
                    f"directory {canonical.relative_to(root)}."
                )
                continue
            if not resolved.exists():
                rep.error(f".claude/skills/{name} is a broken symlink (target {resolved} does not exist)")
            continue
        # A real directory is valid only when declared as harness-written. Claude Code can
        # create these for features such as /verify and /run-skill-generator.
        rel = f".claude/skills/{name}"
        if any(_glob_match(rel, pattern) for pattern in harness_written):
            rep.note(f"{rel} is a harness-written directory, allowed by compatibility.json")
        else:
            rep.stage_error(
                f"{rel} is a real directory but is not declared in harnessWritten. If Claude Code "
                f"wrote it, add a harnessWritten entry; if a human did, move it to "
                f"{canonical.relative_to(root)} and symlink it."
            )


def _glob_match(value: str, pattern: str) -> bool:
    from fnmatch import fnmatch
    return fnmatch(value, pattern) or fnmatch(value, pattern.rstrip("/") + "/*")


# ── check 2: declaration vs filesystem ────────────────────────────────────────────────

def check_declaration(root: Path, compat: dict, rep: Report) -> None:
    optional = set(compat.get("optionalFiles") or [])
    for key, rel in (compat.get("canonical") or {}).items():
        target = root / rel
        if not target.exists() and rel not in optional:
            rep.stage_error(f"compatibility.json declares canonical.{key} = {rel}, which does not exist")

    declared = compat.get("autoMemory")
    settings = read_json(root / ".claude" / "settings.json") or {}
    if "autoMemoryEnabled" in settings:
        actual = "on" if settings["autoMemoryEnabled"] else "off"
    else:
        actual = "on"  # Anthropic ships it on by default
    if declared and declared != actual:
        rep.error(
            f"compatibility.json declares autoMemory = {declared!r} but settings.json yields "
            f"{actual!r}. Update the declaration or the setting so they agree."
        )
    if declared == "off" and not compat.get("autoMemoryReason"):
        rep.error("autoMemory is 'off' but autoMemoryReason is empty. Say what data boundary it protects.")
    if actual == "on":
        rep.note(
            "auto memory is ON: up to 200 lines or 25 KB of machine-local MEMORY.md joins the "
            "Claude-visible startup set, which CI cannot see. That is more than the entire "
            "repo-authored budget. Intended, but it should not be a surprise."
        )

    adapters = compat.get("adapters") or []
    declared_reach: set[tuple[str, str | None]] = set()
    for adapter in adapters if isinstance(adapters, list) else []:
        if not isinstance(adapter, dict):
            continue  # the schema check reports the shape
        harness_name = adapter.get("harness")
        kind = adapter.get("kind")
        source = adapter.get("from")
        target = adapter.get("to")
        declared_reach.add((str(harness_name), source if isinstance(source, str) else None))

        if kind == "native":
            if harness_name == "codex":
                canonical = compat.get("canonical") or {}
                if canonical.get("instructions") != "AGENTS.md":
                    rep.error("Codex native adapter requires canonical.instructions = AGENTS.md")
                if canonical.get("skills") != ".agents/skills":
                    rep.error("Codex native adapter requires canonical.skills = .agents/skills")
            continue

        if kind == "manual-import":
            rep.note(
                f"{harness_name} adapter is manual and cannot be verified from this checkout: "
                f"{adapter.get('note') or 'no verification note declared'}"
            )
            continue

        if not isinstance(source, str) or not isinstance(target, str):
            rep.error(f"declared {kind} adapter for {harness_name} needs string `from` and `to`")
            continue

        if "<name>" in source or "<name>" in target:
            # check_skills resolves every concrete project skill and its Claude symlink.
            if kind != "relative-symlink":
                rep.error(f"placeholder adapter {source} must be a relative-symlink")
            continue

        src = root / source
        expected = (src.parent / target).resolve()
        if kind == "relative-symlink":
            if not src.is_symlink():
                rep.error(f"declared adapter {source} is not a symlink")
                continue
            actual_target = os.readlink(src)
            if os.path.isabs(actual_target):
                rep.error(f"declared adapter {source} is an absolute symlink ({actual_target})")
                continue
            actual = (src.parent / actual_target).resolve()
            if actual != expected:
                rep.error(
                    f"declared adapter {source} resolves to {actual}, not declared target {expected}"
                )
            if not actual.exists():
                rep.error(f"declared adapter {source} is broken (target {actual} does not exist)")
            continue

        if kind == "import":
            if not src.is_file() or src.is_symlink():
                rep.error(f"declared import adapter {source} is missing or is not a real file")
                continue
            directive = "@" + target
            lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
            if directive not in (line.strip() for line in lines):
                rep.error(
                    f"declared import adapter {source} does not contain the exact line {directive!r}"
                )
            if not expected.exists():
                rep.error(f"declared import adapter {source} targets missing path {target}")

    # 0.3.0 makes the reach paths an asserted contract rather than an optional comment.
    if _version_at_least(compat, (0, 3, 0)):
        if ("codex", None) not in declared_reach:
            rep.error("compatibility.json lacks the Codex native adapter declaration")
        shim = (compat.get("canonical") or {}).get("shim", "CLAUDE.md")
        if ("claude-code", shim) not in declared_reach:
            rep.error(f"compatibility.json lacks a Claude adapter for {shim}")
        if ("claude-code", ".claude/skills/<name>") not in declared_reach:
            rep.error("compatibility.json lacks the Claude project-skills symlink adapter")

    for exc in compat.get("exceptions") or []:
        rep.note(f"declared exception: {exc.get('rule')} — {exc.get('reason')} (approved {exc.get('approvedBy')}, {exc.get('date')})")


def check_codex_hooks(root: Path, compat: dict, rep: Report) -> None:
    """Assert the generated Codex adapter's current schema and provider semantics."""
    path = root / ".codex" / "hooks.json"
    enforcement = (compat.get("enforcement") or {}).get("codex", "none")
    native_declared = any(
        isinstance(a, dict) and a.get("harness") == "codex" and a.get("kind") == "native"
        for a in (compat.get("adapters") or [])
    )
    required = _version_at_least(compat, (0, 3, 0)) and (native_declared or enforcement != "none")
    if not path.exists():
        if required:
            rep.error(".codex/hooks.json is missing; run `agentkit apply` to install the Codex adapter")
        return

    config = read_json(path)
    if not isinstance(config, dict):
        rep.error(".codex/hooks.json is not valid JSON")
        return
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        rep.error(".codex/hooks.json has no object-valued `hooks` block")
        return

    expected = (
        ("PreToolUse", "^Bash$", "pretooluse-bash.sh"),
        ("PreToolUse", "^apply_patch$", "pretooluse-write.py"),
        ("PreToolUse", "^mcp__.*$", "pretooluse-mcp.py"),
        ("PostToolUse", "^apply_patch$", "posttooluse-write.py"),
    )
    for event, matcher, script in expected:
        groups = hooks.get(event)
        if not isinstance(groups, list):
            rep.error(f".codex/hooks.json lacks hooks.{event}")
            continue
        matching = [g for g in groups
                    if isinstance(g, dict) and g.get("matcher") == matcher]
        handlers = [h for group in matching for h in (group.get("hooks") or [])
                    if isinstance(h, dict)]
        commands = [h.get("command") for h in handlers
                    if h.get("type") == "command" and isinstance(h.get("command"), str)]
        command = next((cmd for cmd in commands if script in cmd), None)
        if command is None:
            rep.error(
                f".codex/hooks.json {event} matcher {matcher!r} does not invoke {script}"
            )
            continue
        if "AGENTKIT_HARNESS=codex" not in command:
            rep.error(
                f".codex/hooks.json command for {script} lacks AGENTKIT_HARNESS=codex; "
                "the shared gate would emit Claude-only approval decisions"
            )
        if "git rev-parse --show-toplevel" not in command:
            rep.error(
                f".codex/hooks.json command for {script} is not rooted through the git top level"
            )
        installed = root / ".claude" / "hooks" / "agentkit" / script
        if not installed.is_file():
            rep.error(f".codex/hooks.json references missing installed hook {installed.relative_to(root)}")
        elif not os.access(installed, os.X_OK):
            rep.error(f"Codex hook {installed.relative_to(root)} is not executable")

    config_toml = root / ".codex" / "config.toml"
    if config_toml.exists():
        text = config_toml.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^\s*(?:codex_)?hooks\s*=\s*false\b", text, re.M | re.I):
            rep.error(".codex/config.toml disables hooks, so the installed adapter cannot run")
        if re.search(r"^\s*allow_managed_hooks_only\s*=\s*true\b", text, re.M | re.I):
            rep.error(
                ".codex/config.toml allows managed hooks only, which skips this project adapter"
            )

    if enforcement == "blocking":
        evidence = (compat.get("enforcementEvidence") or {}).get("codex")
        if not isinstance(evidence, dict):
            rep.error(
                "Codex is declared blocking but has no live enforcementEvidence. "
                "Run `agentkit self-test --repo .` with Codex installed."
            )
        else:
            actual_digest = codex_adapter_digest(root)
            if actual_digest is None or evidence.get("adapterSha256") != actual_digest:
                rep.error(
                    "Codex blocking evidence is stale: installed hook/config bytes changed. "
                    "Re-run `agentkit self-test --repo .`."
                )
            try:
                proc = subprocess.run(["codex", "--version"], capture_output=True, text=True,
                                      timeout=10)
            except FileNotFoundError:
                rep.note("Codex binary unavailable here; recorded live evidence could not be version-checked")
            except subprocess.TimeoutExpired:
                rep.error("Codex is installed but `codex --version` timed out; blocking evidence cannot be checked")
            else:
                version = proc.stdout.strip()
                if proc.returncode != 0 or not version:
                    rep.error("Codex is installed but its version could not be read; blocking evidence cannot be checked")
                elif version != evidence.get("harnessVersion"):
                    rep.error(
                        f"Codex blocking evidence was measured on {evidence.get('harnessVersion')!r}, "
                        f"but this machine runs {version!r}. Re-run the live self-test."
                    )
                else:
                    check_checkout_evidence(root, "codex", evidence, compat, rep)
        rep.note(
            "Codex project hooks are blocking only after the workspace and current hook hash "
            "are trusted on this machine; inspect or approve them with `/hooks`."
        )


def check_claude_hooks(root: Path, compat: dict, rep: Report) -> None:
    """Assert the Claude wire path, and that a `blocking` claim still has live evidence.

    The evidence is bound to bytes and to a version. Either can move without anyone
    touching the declaration -- a hook edit, a kit re-apply, a CLI upgrade -- and each of
    those makes the recorded run a statement about software that is no longer installed.
    """
    settings_path = root / ".claude" / "settings.json"
    settings = read_json(settings_path)
    enforcement = (compat.get("enforcement") or {}).get("claude-code")
    hook_dir = root / ".claude" / "hooks" / "agentkit"

    # A disarm switch in a repo that ships gates is worth an error whatever the declaration
    # says: the hooks are installed, reviewable, and doing nothing.
    if hook_dir.is_dir():
        for rel in (".claude/settings.json", ".claude/settings.local.json"):
            data = read_json(root / rel)
            if isinstance(data, dict) and data.get("disableAllHooks"):
                rep.error(
                    f"{rel} sets disableAllHooks, so every installed RepoCharter gate in this "
                    "checkout is inert. Remove the switch, or remove the hooks deliberately."
                )

    if enforcement != "blocking":
        return

    if not isinstance(settings, dict):
        rep.error(
            "Claude Code is declared blocking but .claude/settings.json is missing or is not "
            "valid JSON, so no hook is wired."
        )
        return

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        rep.error(".claude/settings.json has no object-valued `hooks` block")
        return
    for event, matcher, script in CLAUDE_HOOK_WIRING:
        groups = hooks.get(event)
        if not isinstance(groups, list):
            rep.error(f".claude/settings.json lacks hooks.{event}")
            continue
        matching = [g for g in groups
                    if isinstance(g, dict) and (matcher is None or g.get("matcher") == matcher)]
        commands = [h.get("command") for group in matching for h in (group.get("hooks") or [])
                    if isinstance(h, dict) and h.get("type") == "command"
                    and isinstance(h.get("command"), str)]
        expected_command = f"${{CLAUDE_PROJECT_DIR}}/.claude/hooks/agentkit/{script}"
        if expected_command not in commands:
            rep.error(
                f".claude/settings.json {event} matcher {matcher or '(any)'!r} does not invoke {script}"
            )
            continue
        installed = hook_dir / script
        if not installed.is_file():
            rep.error(f".claude/settings.json references missing installed hook {script}")
        elif not os.access(installed, os.X_OK):
            rep.error(f"Claude hook .claude/hooks/agentkit/{script} is not executable")

    evidence = (compat.get("enforcementEvidence") or {}).get("claude-code")
    if not isinstance(evidence, dict):
        rep.error(
            "Claude Code is declared blocking but has no live enforcementEvidence. "
            "Run `agentkit self-test --repo . --promote-claude` with Claude Code installed."
        )
        return

    actual_digest = claude_adapter_digest(root)
    if actual_digest is None or evidence.get("adapterSha256") != actual_digest:
        rep.error(
            "Claude blocking evidence is stale: installed settings/hook bytes changed. "
            "Re-run `agentkit self-test --repo . --promote-claude`."
        )
    try:
        proc = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        rep.note("Claude binary unavailable here; recorded live evidence could not be version-checked")
    except subprocess.TimeoutExpired:
        rep.error("Claude is installed but `claude --version` timed out; blocking evidence cannot be checked")
    else:
        version = proc.stdout.strip()
        if proc.returncode != 0 or not version:
            rep.error("Claude is installed but its version could not be read; blocking evidence cannot be checked")
        elif version != evidence.get("harnessVersion"):
            rep.error(
                f"Claude blocking evidence was measured on {evidence.get('harnessVersion')!r}, "
                f"but this machine runs {version!r}. Re-run the live self-test."
            )
        else:
            check_checkout_evidence(root, "claude-code", evidence, compat, rep)
            trust_root = claude_trust_root(root)
            if trust_root is None:
                rep.error(
                    "Claude is declared blocking but no persisted workspace trust covers this "
                    "checkout. Start `claude` interactively here, review and trust the project, "
                    "then rerun the live self-test."
                )
            settings_paths = [root / ".claude" / "settings.json",
                              *claude_local_settings_paths(root),
                              claude_config_dir() / "settings.json"]
            seen: set[Path] = set()
            for candidate in settings_paths:
                candidate = candidate.resolve()
                if candidate in seen or not candidate.exists():
                    continue
                seen.add(candidate)
                effective = read_json(candidate)
                if not isinstance(effective, dict):
                    rep.error(
                        f"Claude effective settings at {candidate} are not valid object-valued "
                        "JSON, so hook state is unknown."
                    )
                elif effective.get("disableAllHooks") is True:
                    rep.error(f"{candidate} sets disableAllHooks, so Claude project hooks are inert")
            if os.environ.get("CLAUDE_CODE_SAFE_MODE"):
                rep.error("CLAUDE_CODE_SAFE_MODE is set, so Claude project hooks are disabled")
    rep.note(
        "Claude hooks are read once, at session start. A session older than the current "
        "settings.json is running the previous gates; restart it before trusting this claim."
    )


def check_precommit(root: Path, rep: Report) -> None:
    """Assert that Git invokes a complete, reachable RepoCharter commit gate."""
    try:
        in_git = subprocess.run(
            ["git", "rev-parse", "--git-dir"], cwd=root,
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        rep.error("could not inspect Git pre-commit wiring")
        return
    if in_git.returncode != 0:
        rep.note("not a Git checkout; pre-commit wiring cannot be verified")
        return

    configured = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"], cwd=root,
        capture_output=True, text=True, timeout=15,
    ).stdout.strip()
    if not configured:
        rep.error(
            "git core.hooksPath is unset, so the checked-in RepoCharter pre-commit gate "
            "does not run. Run `agentkit apply`."
        )
        return

    hooks_dir = Path(configured).expanduser()
    if not hooks_dir.is_absolute():
        hooks_dir = root / hooks_dir
    hook = hooks_dir / "pre-commit"
    try:
        display = hook.resolve().relative_to(root.resolve())
    except ValueError:
        display = hook
    if not hook.is_file():
        rep.error(f"active pre-commit hook is missing at {display}")
        return
    if not os.access(hook, os.X_OK):
        rep.error(f"active pre-commit hook is not executable: {display}")

    text = hook.read_text(encoding="utf-8", errors="replace")
    starts, ends = text.count(PRECOMMIT_BEGIN), text.count(PRECOMMIT_END)
    if starts != 1 or ends != 1:
        rep.error(
            f"active pre-commit hook has {starts} RepoCharter start marker(s) and "
            f"{ends} end marker(s); run `agentkit apply` to install one complete block"
        )
        return
    begin, end = text.index(PRECOMMIT_BEGIN), text.index(PRECOMMIT_END)
    if end < begin:
        rep.error("active pre-commit hook's RepoCharter markers are reversed")
        return
    managed = text[begin:end]
    if "python3 kit/agentkit verify --repo ." not in managed:
        rep.error("active pre-commit RepoCharter block does not invoke `agentkit verify`")

    for number, line in enumerate(text[:begin].splitlines(), 1):
        if re.match(r"^[ \t]*exit(?:[ \t]|$)", line):
            rep.error(
                f"active pre-commit RepoCharter block is unreachable: line {number} exits "
                "before the managed block. Run `agentkit apply` to reposition it."
            )
            break


def check_validation(root: Path, compat: dict, rep: Report) -> None:
    """Run the repo-declared clean-code commands without a shell."""
    validation = compat.get("validation") or {}
    commands = validation.get("commands") if isinstance(validation, dict) else None
    if not commands:
        return
    if not isinstance(commands, list):
        return  # schema check reports it
    for spec in commands:
        if not isinstance(spec, dict):
            continue
        name, argv = spec.get("name"), spec.get("argv")
        if not isinstance(name, str) or not isinstance(argv, list) or not argv:
            continue
        if not all(isinstance(arg, str) and arg for arg in argv):
            continue
        timeout = spec.get("timeoutSeconds", 300)
        try:
            proc = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            rep.error(f"validation {name!r} could not run: executable {argv[0]!r} was not found")
            continue
        except subprocess.TimeoutExpired:
            rep.error(f"validation {name!r} timed out after {timeout} seconds")
            continue
        if proc.returncode != 0:
            lines = [line for line in (proc.stdout + "\n" + proc.stderr).splitlines() if line.strip()]
            tail = " | ".join(lines[-4:])
            rep.error(
                f"validation {name!r} failed with exit {proc.returncode}"
                + (f": {tail}" if tail else "")
            )
        else:
            rep.note(f"validation passed: {name}")


# ── check 3: dead references in settings.json ─────────────────────────────────────────

def check_dead_references(root: Path, rep: Report) -> None:
    for name in ("settings.json", "settings.local.json"):
        path = root / ".claude" / name
        settings = read_json(path)
        if settings is None:
            if path.exists():
                rep.error(f".claude/{name} is not valid JSON")
            continue

        referenced: list[tuple[str, str]] = []
        for event, groups in (settings.get("hooks") or {}).items():
            for group in groups if isinstance(groups, list) else []:
                for hook in group.get("hooks", []) if isinstance(group, dict) else []:
                    if hook.get("type") == "command" and hook.get("command"):
                        referenced.append((f"hooks.{event}", hook["command"]))
        # The half everything else missed.
        for entry in (settings.get("permissions") or {}).get("allow") or []:
            match = re.match(r"^Bash\((.+?)(?::\*)?\)$", entry)
            if match and ("/" in match.group(1) or match.group(1).startswith("$")):
                referenced.append(("permissions.allow", match.group(1)))

        # An interpreter prefix hides the real target: `bash "$CLAUDE_PROJECT_DIR/x.sh"`
        # has `bash` as its first token. Skip interpreter tokens before checking the script
        # path.
        INTERPRETERS = {"bash", "sh", "zsh", "python", "python3", "node", "npx", "uv", "env"}

        for where, raw in referenced:
            tokens = [t.strip('"\'') for t in raw.split()]
            command = next((t for t in tokens
                            if t and not t.startswith("-")
                            and Path(t).name not in INTERPRETERS), tokens[0] if tokens else raw)
            resolved = command.replace("${CLAUDE_PROJECT_DIR}", str(root)).replace("$CLAUDE_PROJECT_DIR", str(root))
            if not resolved.startswith("/"):
                continue
            target = Path(resolved)
            # Only police paths the REPO owns. A system binary that is absent here may be
            # present in CI — /usr/bin/lsof exists on Linux and not on macOS — so flagging
            # it produces a finding that passes in one environment and fails in the other,
            # which is the "works on my machine" inconsistency this checker exists to remove.
            try:
                target.resolve().relative_to(root.resolve())
            except ValueError:
                continue
            if not target.exists():
                rep.error(
                    f".claude/{name} {where} references {command}, which does not exist. "
                    "A dead reference in an allowlist is invisible to hook-only checkers."
                )
            elif where.startswith("hooks.") and not os.access(target, os.X_OK):
                rep.error(f".claude/{name} {where} references {command}, which is not executable")


# ── check 4 and 5: amputation vectors ─────────────────────────────────────────────────

def check_codex_config(root: Path, rep: Report) -> None:
    path = root / ".codex" / "config.toml"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"^\s*project_doc_max_bytes\s*=", text, re.M):
        rep.error(
            ".codex/config.toml sets project_doc_max_bytes. This key is honored once the "
            "workspace is trusted, and nothing in Codex's project-local denylist "
            "blocks it. One committed line silently truncates this repo's own AGENTS.md for "
            "every developer. Remove it."
        )
    if re.search(r"^\s*project_doc_fallback_filenames\s*=", text, re.M):
        rep.error(
            ".codex/config.toml declares project_doc_fallback_filenames. In directories that "
            "lack AGENTS.md this substitutes another file into the instruction chain. Remove it."
        )


def check_claude_md_excludes(root: Path, rep: Report) -> None:
    for name in ("settings.json", "settings.local.json"):
        settings = read_json(root / ".claude" / name) or {}
        excludes = settings.get("claudeMdExcludes")
        if excludes:
            rep.error(
                f".claude/{name} sets claudeMdExcludes ({excludes}). This is the Claude-side "
                "amputation vector: a committed settings file can silently drop this repo's own "
                "CLAUDE.md from the session. If a monorepo genuinely needs it, record an "
                "exception in compatibility.json so it is visible."
            )


def check_override_and_nesting(root: Path, rep: Report) -> None:
    """Both are also agnix rules; both are repeated here because both are silent and fatal."""
    for override in root.rglob("AGENTS.override.md"):
        if ".git" in override.parts:
            continue
        rep.error(
            f"{override.relative_to(root)} exists. Codex uses an override file instead of "
            "AGENTS.md at that directory level; it does not merge the two."
        )
    roots = [p for p in root.rglob("AGENTS.md")
             if ".git" not in p.parts and "node_modules" not in p.parts]
    nested = [p for p in roots if p.parent != root]
    if nested:
        rep.stage_error(
            "nested AGENTS.md found: " + ", ".join(str(p.relative_to(root)) for p in nested) +
            ". ZCode does not merge across directory levels, and Codex consumes the chain "
            "root-first so the deepest file -- the one the standard says overrides -- is the "
            "one truncated. One flat root file is the only shape all three harnesses agree on."
        )


# Frontmatter keys from OTHER tools that express "do not always load me". Claude Code does
# not read any of them — it reads `paths:` — so a file carrying one and no `paths:` states an
# intent the harness silently ignores.
FOREIGN_ONDEMAND_KEYS = ("alwaysApply", "globs", "applyTo", "auto_attach", "trigger")


def check_rules(root: Path, rep: Report) -> None:
    rules_dir = root / ".claude" / "rules"
    if not rules_dir.is_dir():
        return
    intent_mismatch = []
    for rule in sorted(rules_dir.rglob("*.md")):
        meta = frontmatter(rule.read_text(encoding="utf-8", errors="replace")) or {}

        # A foreign on-demand key records scope intent in a dialect Claude Code does not
        # read. Report it separately from an instruction file with no scope metadata.
        foreign = [k for k in FOREIGN_ONDEMAND_KEYS if k in meta]
        if foreign and not meta.get("paths"):
            intent_mismatch.append((rule.relative_to(root), rule.stat().st_size, foreign[0]))

        if not meta.get("paths"):
            rep.stage_error(
                f"{rule.relative_to(root)} has no `paths:` frontmatter. Rules without paths are "
                "loaded at launch with the same priority as CLAUDE.md -- this is exactly the "
                "always-loaded surface the retired system filled. Scope it or delete it."
            )

    if intent_mismatch:
        total = sum(size for _, size, _ in intent_mismatch)
        rep.error(
            f"{len(intent_mismatch)} rule file(s), {total:,} bytes, declare on-demand loading in a "
            f"key Claude Code does not read (`{intent_mismatch[0][2]}:`) while carrying no "
            f"`paths:`. The intent is already recorded — in the wrong dialect — so these load in "
            f"FULL every session: "
            + ", ".join(f"{p} ({s:,}B)" for p, s, _ in intent_mismatch[:4])
            + (f", +{len(intent_mismatch) - 4} more" if len(intent_mismatch) > 4 else "")
        )


# ── budgets ───────────────────────────────────────────────────────────────────────────

def check_budgets(root: Path, compat: dict, rep: Report) -> None:
    budgets = compat.get("budgets") or {}
    max_bytes = budgets.get("codexProjectDocMaxBytes", 32768)
    line_target = budgets.get("claudeLineTarget", 200)

    agents = root / (compat.get("canonical", {}).get("instructions") or "AGENTS.md")
    if agents.exists():
        size = agents.stat().st_size
        pct = 100 * size / max_bytes
        rep.note(f"Codex-visible chain: {size:,} bytes ({pct:.1f}% of the {max_bytes:,}-byte cap)")
        if size > max_bytes:
            rep.error(
                f"{agents.name} is {size:,} bytes, over Codex's {max_bytes:,}-byte project "
                "instruction cap. The tail is truncated SILENTLY on the user surface, so the "
                "rules at the bottom of the file simply stop existing."
            )

    claude_files, lines, total = [], 0, 0
    for candidate in (root / "CLAUDE.md", root / ".claude" / "CLAUDE.md"):
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8", errors="replace")
            claude_files.append(candidate.name)
            lines += len(text.splitlines())
            total += candidate.stat().st_size
            if re.search(r"^@\S+", text, re.M) and agents.exists():
                lines += len(agents.read_text(encoding="utf-8", errors="replace").splitlines())
                total += agents.stat().st_size

    # Unscoped .claude/rules count too, and they are the half that hides. Anthropic:
    # "Rules without `paths` frontmatter are loaded at launch with the same priority as
    # `.claude/CLAUDE.md`." Counting only CLAUDE.md and AGENTS.md understates the real
    # startup cost of repositories whose legacy rule directories contain unscoped files.
    rules_dir = root / ".claude" / "rules"
    unscoped_bytes = 0
    if rules_dir.is_dir():
        for rule in sorted(rules_dir.rglob("*.md")):
            meta = frontmatter(rule.read_text(encoding="utf-8", errors="replace")) or {}
            if meta.get("paths"):
                continue
            text = rule.read_text(encoding="utf-8", errors="replace")
            unscoped_bytes += rule.stat().st_size
            total += rule.stat().st_size
            lines += len(text.splitlines())
            claude_files.append(str(rule.relative_to(root)))
    if unscoped_bytes:
        rep.note(f"of which {unscoped_bytes:,} bytes are UNSCOPED .claude/rules — always-loaded, easy to miss")

    if claude_files:
        rep.note(f"Claude-visible repo-controlled set: {total:,} bytes / {lines} lines")
        if lines > line_target:
            rep.warn(
                f"the Claude-visible set is {lines} lines, over the {line_target}-line target. "
                "Anthropic publishes no byte cap here, only that shorter files produce better "
                "adherence -- so this is a warning, not a failure. Move something to a "
                "path-scoped rule or a skill."
            )

    pitfalls = root / "docs" / "project" / "pitfalls.md"
    if pitfalls.exists():
        text = pitfalls.read_text(encoding="utf-8", errors="replace")
        n_lines, n_bytes = len(text.splitlines()), pitfalls.stat().st_size
        cap_lines = budgets.get("pitfallsMaxLines", 100)
        cap_bytes = budgets.get("pitfallsMaxBytes", 8192)
        if n_lines > cap_lines or n_bytes > cap_bytes:
            rep.error(
                f"pitfalls.md is {n_lines} lines / {n_bytes:,} bytes, over its "
                f"{cap_lines}-line / {cap_bytes:,}-byte cap. The cap is the mechanism: evict an "
                "entry whose failure a gate now catches, citing the gate, before adding another."
            )


# ── the effective ledger ──────────────────────────────────────────────────────────────

def check_effective(root: Path, rep: Report) -> None:
    """Diff the instruction text Codex ACTUALLY loads against what the repo declares.

    This asserts against the harness's own ledger rather than a model of it, which catches
    an override file, a fallback filename, a lowered cap and a truncation at once.

    `codex debug prompt-input` depends on machine-local workspace trust. An untrusted
    checkout cannot observe trusted-workspace configuration, so this is a developer-machine
    check; ``check_codex_config`` provides the static CI check.
    """
    agents = root / "AGENTS.md"
    if not agents.exists():
        return
    try:
        proc = subprocess.run(
            ["codex", "debug", "prompt-input"],
            cwd=root, capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        rep.warn(f"--effective skipped: could not run `codex debug prompt-input` ({exc})")
        return
    if proc.returncode != 0:
        rep.warn(f"--effective skipped: codex exited {proc.returncode}")
        return

    prompt = proc.stdout
    text = agents.read_text(encoding="utf-8", errors="replace")
    head = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    tail = next((ln.strip() for ln in reversed(text.splitlines()) if ln.strip()), "")

    if head and head not in prompt:
        rep.error(
            "the first line of AGENTS.md is ABSENT from Codex's model-visible prompt. The most "
            "likely causes are an AGENTS.override.md or a fallback filename substitution."
        )
    elif tail and tail not in prompt:
        rep.error(
            "the first line of AGENTS.md is present in Codex's prompt but the LAST line is not: "
            "the file is being truncated. Codex keeps the head and drops the tail, so the rules "
            "at the bottom are gone. Shrink the file or move rules upward."
        )
    else:
        rep.note("effective ledger: AGENTS.md enters Codex's prompt head-to-tail, untruncated")
    rep.note(
        "--effective reflects THIS machine's workspace-trust state. An untrusted checkout "
        "cannot observe a .codex/config.toml amputation; the static check is the CI gate."
    )


# ── entry point ───────────────────────────────────────────────────────────────────────

def run(root: Path, effective: bool, strict: bool) -> Report:
    rep = Report()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    try:
        import stage as stage_lib
        rep.stage, markers = stage_lib.detect(root)
        rep.note(f"stage: {rep.stage} — {stage_lib.STAGE_HELP[rep.stage]}")
        for line in stage_lib.summarise(rep.stage, markers):
            rep.note(f"  {line}")
        if rep.stage != stage_lib.NOT_STARTED and markers.policy_empty:
            # An empty repository policy is valid because the universal floor remains
            # active. Report it as configuration context rather than a warning.
            rep.note(
                "policy is empty, which may well be right. The universal floor is active "
                "regardless: force-push to a protected branch, checks-bypass, destructive "
                "database operations, recursive rm outside safe paths, and history-losing "
                "git. A policy adds what no catalogue can guess — production deploys, "
                "credential strings, writes to live data. If this repo has none of those, "
                "empty is correct."
            )
    except ImportError:
        pass

    compat_path = root / ".agents" / "compatibility.json"
    compat = read_json(compat_path)
    if compat is None:
        if compat_path.exists():
            rep.error(".agents/compatibility.json is not valid JSON")
            return rep
        rep.error(".agents/compatibility.json is missing. Run `agentkit apply` first.")
        return rep

    check_schema(root, compat, rep)

    # An inert deny rule reads exactly like a working one from the outside, so this is an
    # error rather than a warning: a pattern that silently matches nothing occupies the slot
    # where a real fence would go.
    try:
        import scaffold as scaffold_lib
        for problem in scaffold_lib.lint_policy(compat.get("policy") or {}):
            # The unsatisfiable-conjunction check is heuristic: failure to synthesize a
            # witness does not prove that none exists. Warn rather than fail, and rely on the
            # source control-flow parser plus live measurement for definitive results.
            if "CAN NEVER FIRE" in problem:
                rep.warn(f"POSSIBLY INERT (heuristic) — {problem} "
                         "Verify by running the command its reason names before acting.")
            else:
                rep.error(f"INERT RULE — {problem}")
    except ImportError:
        pass

    if (compat.get("policyDraft") or {}).get("needsManualPattern"):
        n = len(compat["policyDraft"]["needsManualPattern"])
        rep.warn(
            f"{n} harvested rule(s) could not be turned into a portable pattern and were NOT "
            "promoted — see policyDraft.needsManualPattern. Each one is a fence this repo "
            "believes it has and does not."
        )

    check_declaration(root, compat, rep)
    check_skills(root, compat, rep)
    check_dead_references(root, rep)
    check_codex_hooks(root, compat, rep)
    check_claude_hooks(root, compat, rep)
    check_precommit(root, rep)
    check_codex_config(root, rep)
    check_claude_md_excludes(root, rep)
    check_override_and_nesting(root, rep)
    check_rules(root, rep)
    check_budgets(root, compat, rep)
    check_validation(root, compat, rep)
    if effective:
        check_effective(root, rep)

    if strict and rep.warnings:
        rep.errors.extend(f"(strict) {w}" for w in rep.warnings)
        rep.warnings.clear()
    return rep


def main() -> int:
    parser = argparse.ArgumentParser(description="agentkit conformance residue checks")
    parser.add_argument("--repo", default=".", help="repository root (default: cwd)")
    parser.add_argument("--effective", action="store_true", help="also diff Codex's real prompt ledger")
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = parser.parse_args()

    root = Path(args.repo).resolve()
    if not root.is_dir():
        print(f"agentkit: {root} is not a directory", file=sys.stderr)
        return 2

    rep = run(root, args.effective, args.strict)

    if args.json:
        print(json.dumps({"stage": rep.stage, "errors": rep.errors, "warnings": rep.warnings,
                          "todos": rep.todos, "notes": rep.notes}, indent=2))
    else:
        for note in rep.notes:
            print(f"  note: {note}")
        for todo in rep.todos:
            print(f"  TODO: {todo}")
        for warning in rep.warnings:
            print(f"  WARN: {warning}")
        for error in rep.errors:
            print(f" ERROR: {error}")
        print()
        summary = (f"agentkit residue [{rep.stage}]: {len(rep.errors)} error(s), "
                   f"{len(rep.warnings)} warning(s), {len(rep.todos)} migration todo(s)")
        print(summary)
        if rep.todos and not rep.errors:
            print("  Nothing is broken. The todos are migration work — run `agentkit migrate` "
                  "for a plan.")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
