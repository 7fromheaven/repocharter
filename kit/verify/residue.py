#!/usr/bin/env python3
"""agentkit conformance checks — the part no shipped linter covers.

agnix carries 445 sourced rules and already implements the byte-chain check, the
override-file check, the nested-AGENTS.md check, import validity, and the rules-frontmatter
checks. It is the right tool and this file does not re-implement it.

What lives here is the residue: the checks that exist in no rule catalogue, plus the ones
that must read `.agents/compatibility.json` to know what this repo declared. Every check
below was written because something actually slipped past everything else:

  1. Symlink conformance for the .claude/skills adapter, tolerating the real directories
     Claude Code writes there itself. A verifier that errored on those broke the first
     time someone used a shipped feature -- a live bug in the reference repo.
  2. The declaration matching the filesystem, including autoMemory against settings.json.
     A declaration nobody checks is how drift starts.
  3. Dead references across settings.json permissions.allow, not just hooks blocks. Four
     dead entries sat in the reference repo passing its own validator AND agnix at zero
     errors, because all of them looked only at hooks blocks.
  4. A repo .codex/config.toml lowering project_doc_max_bytes or declaring fallback
     filenames. Measured honoured in a trusted workspace, present in no rule catalogue,
     and a single committed line silently amputates the repo's own safety file.
  5. claudeMdExcludes, the same amputation shape on the Claude side.

Exit codes: 0 clean or warnings only, 1 errors found, 2 the checker could not run.
"""

from __future__ import annotations

import argparse
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
# Verbatim from code.claude.com/docs/en/skills, retrieved 2026-08-08. The earlier list held
# three of these and errored on the rest — which meant a real project skill using
# `user-invocable: true` hard-failed the verifier. A rejection list built from memory rather
# than from the published table is how a linter starts failing valid work, and a linter that
# fails valid work gets switched off.
CLAUDE_ONLY_SKILL_FIELDS = {
    "paths", "disable-model-invocation", "user-invocable", "argument-hint", "arguments",
    "disallowed-tools", "model", "effort", "context", "agent", "background", "hooks",
}

RETIRED_FIELDBOOK_SKILLS = {
    "checkpoint", "handoff", "lessons", "plan-sync", "state-router",
    "dispatch-gate", "frontmatter-lint", "id-spine",
}

# Paths Claude Code writes into .claude/skills ITSELF, tolerated whether or not a repo
# remembered to declare them.
#
# This is a built-in floor rather than a template default on purpose. The bug this replaces
# hard-failed the reference repo's validator the first time a shipped feature was used
# (`/verify` commits .claude/skills/verify/SKILL.md, `/run-skill-generator` commits
# .claude/skills/run-<name>/), and a fix that depends on every one of 41 repos remembering
# a config key is a fix that comes back. compatibility.json `harnessWritten` ADDS to this
# set; it cannot shrink it.
DEFAULT_HARNESS_WRITTEN = (".claude/skills/verify", ".claude/skills/run-*")


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

        Without this split, ten un-migrated repositories produce ninety identical-looking
        errors and there is no way to tell progress from failure -- which is the same as
        having no signal.
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
        if name in RETIRED_FIELDBOOK_SKILLS:
            rep.stage_error(
                f".claude/skills/{name} is a retired fieldbook skill. Delete it; its procedure "
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
        # A real directory. Legal only when declared as harness-written -- Claude Code
        # creates these itself (/verify, /run-skill-generator) and erroring on them turned
        # one use of a shipped feature into a hard validator failure.
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
            f"{actual!r}. One of them is lying, and this check is what makes either choice honest."
        )
    if declared == "off" and not compat.get("autoMemoryReason"):
        rep.error("autoMemory is 'off' but autoMemoryReason is empty. Say what data boundary it protects.")
    if actual == "on":
        rep.note(
            "auto memory is ON: up to 200 lines or 25 KB of machine-local MEMORY.md joins the "
            "Claude-visible startup set, which CI cannot see. That is more than the entire "
            "repo-authored budget. Intended, but it should not be a surprise."
        )

    for adapter in compat.get("adapters") or []:
        if adapter.get("kind") != "relative-symlink":
            continue
        src = root / (adapter.get("from") or "")
        if not src.exists() and not src.is_symlink():
            rep.error(f"declared adapter {adapter.get('from')} does not exist")

    for exc in compat.get("exceptions") or []:
        rep.note(f"declared exception: {exc.get('rule')} — {exc.get('reason')} (approved {exc.get('approvedBy')}, {exc.get('date')})")


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
        # has `bash` as its first token, so first-token logic checks the wrong thing — it
        # misses a deleted script, and anything that PRUNES on that basis deletes a live
        # hook. Both happened: this checker silently skipped such hooks, and an ad-hoc sweep
        # built on the same assumption removed a working budget gate.
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
            ".codex/config.toml sets project_doc_max_bytes. Measured: this key is honoured "
            "once the workspace is trusted, and nothing in Codex's project-local denylist "
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
            f"{override.relative_to(root)} exists. Measured on codex-cli 0.147.0: an override "
            "file makes AGENTS.md vanish from the model prompt ENTIRELY, not merge with it."
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

        # The author SAID on-demand, in a key this harness does not read. Reported
        # separately because it is a different failure from simply forgetting to scope a
        # file: the intent is already recorded, in the wrong dialect. Measured on one repo:
        # 15 files and 45,556 bytes — 44% of its entire always-loaded set — every one of
        # them marked `alwaysApply: false` and loaded in full on every session.
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
    # startup cost of exactly the repos that most need measuring -- a fieldbook repo can
    # carry more always-loaded bytes here than in both canonical files combined.
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

    IT HAS A MEASURED FALSE NEGATIVE, and the note below is not decoration. `codex debug
    prompt-input` reports different content depending on machine-local workspace trust: an
    untrusted checkout -- which is what a CI runner is -- cannot see a .codex/config.toml
    that amputates AGENTS.md. So this is a developer-machine check, and the static
    check_codex_config above is the one that must be mandatory in CI. They are not parallel.
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
            # This was a WARNING, and the wording said the gates "enforce nothing".
            # That was true when the universal rules were thinner. It is now false and
            # actively misleading: an empty policy still gets the whole universal floor,
            # and for a repo that deploys nothing and holds no production data an empty
            # policy is the CORRECT state rather than a defect. A warning nobody can act
            # on correctly is noise, and noise is what gets a checker switched off.
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

    # An inert deny rule reads exactly like a working one from the outside, so this is an
    # error rather than a warning: a pattern that silently matches nothing occupies the slot
    # where a real fence would go.
    try:
        import scaffold as scaffold_lib
        for problem in scaffold_lib.lint_policy(compat.get("policy") or {}):
            # The unsatisfiable-conjunction check is HEURISTIC and has both error modes. It
            # synthesises probe strings from each pattern and asks whether any satisfies all
            # of them; when it cannot construct a witness it reports inert, and it was
            # measured calling nine WORKING rules broken (`npm publish` satisfies both halves
            # of a manager+verb conjunction that the synthesiser could not build). So it
            # warns rather than fails — a check that fails valid work is one people switch
            # off, and correctness at harvest time now comes from reading the source's actual
            # `&&`/`||` joiner instead of guessing after the fact.
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
    check_codex_config(root, rep)
    check_claude_md_excludes(root, rep)
    check_override_and_nesting(root, rep)
    check_rules(root, rep)
    check_budgets(root, compat, rep)
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
