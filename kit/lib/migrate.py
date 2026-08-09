"""Migration planning: do the mechanical parts, write down the judgment calls.

The division is the whole point, so it is worth stating precisely.

MECHANICAL means the correct action is fully determined by what is on disk, and the tool
can perform it without reading anyone's mind: neutralising a nested AGENTS.md inside an
archive, creating the canonical directory skeleton, declaring a harness-written directory.
These are applied with --apply.

JUDGMENT means the right answer depends on what the content MEANS: which of six fieldbook
skills is a real procedure worth porting, how a 12,950-byte rules file splits into
path-scoped pieces, what in 1,446 archived files is durable project truth. The tool refuses
to guess and writes a plan instead, with the evidence needed to decide.

NOTHING IS EVER DELETED. Mechanical actions quarantine into .agents/quarantine/<stamp>/
with a manifest, so `agentkit revert` can put everything back. A migration you cannot undo
is one nobody will run on the tenth repository.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from stage import RETIRED_SKILLS, detect, scan  # noqa: F401

CANONICAL_FILES = ("SPEC.md", "PLAN.md", "STATUS.md", "pitfalls.md")


@dataclass
class Action:
    kind: str
    target: str
    detail: str
    reversible: str = "quarantined; `agentkit revert` restores"


@dataclass
class Decision:
    kind: str
    subject: str
    question: str
    evidence: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)


@dataclass
class Plan:
    repo: str
    stage: str
    mechanical: list[Action] = field(default_factory=list)
    judgment: list[Decision] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.mechanical and not self.judgment


def _quarantine_dir(root: Path, stamp: str) -> Path:
    return quarantine_root(root, stamp)

def quarantine_root(repo: Path, stamp: str) -> Path:
    """Where retired content goes: OUTSIDE the repository.

    In-tree quarantine loses. `.gitignore` keeps it out of git but not out of the repo's own
    tooling: on one repo the pre-commit eslint gate began reporting unused-variable warnings
    from quarantined hook fixtures, and eslint's flat config does not read .gitignore.
    Prettier, tsc, ruff and every doc-linter each have their own ignore mechanism, so
    in-tree means fighting all of them in all 14 repositories.

    Out-of-tree means no in-repo tool can see it, no ignore entry is needed anywhere, and
    the retirement still shows up in git as a plain deletion — with the originals recoverable
    from history regardless.
    """
    state = Path(os.environ.get("AGENTKIT_STATE_DIR") or (Path.home() / ".agentkit"))
    return state / "quarantine" / repo.resolve().name / stamp



def build(root: Path) -> Plan:
    stage, m = detect(root)
    plan = Plan(repo=str(root), stage=stage)

    # ── mechanical ────────────────────────────────────────────────────────────────────
    for nested in m.nested_agents:
        plan.mechanical.append(Action(
            kind="neutralise-nested-agents",
            target=nested,
            detail=(
                "Rename to AGENTS.archived.md. Codex consumes the root-to-cwd chain and ZCode "
                "does not merge across levels, so a nested file means three different things in "
                "three harnesses. Renaming keeps the content and removes it from every chain."
            ),
            reversible="rename back",
        ))

    if not m.has_project_dir:
        plan.mechanical.append(Action(
            kind="create-canonical-skeleton",
            target="docs/project/",
            detail=(
                "Create the directory with SPEC.md, PLAN.md, STATUS.md and pitfalls.md from the "
                "kit templates, plus decisions/. Content is NOT written — these are empty homes, "
                "and filling them is a judgment call below."
            ),
            reversible="delete the directory if it is still empty",
        ))

    skills_dir = root / ".claude" / "skills"
    if skills_dir.is_dir():
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir() or entry.is_symlink():
                continue
            if entry.name in RETIRED_SKILLS:
                continue  # a judgment call, below — some hold real procedures
            plan.mechanical.append(Action(
                kind="declare-harness-written",
                target=f".claude/skills/{entry.name}",
                detail=(
                    "Add to harnessWritten in compatibility.json. A real directory here is legal "
                    "when the harness created it; declaring it is what stops the verifier failing "
                    "on a shipped feature."
                ),
                reversible="remove the declaration",
            ))

    # ── judgment ──────────────────────────────────────────────────────────────────────
    for name in m.retired_skills:
        skill = skills_dir / name
        body = ""
        spec = skill / "SKILL.md"
        if spec.exists():
            body = spec.read_text(encoding="utf-8", errors="replace")
        size = len(body)
        first_line = next((ln for ln in body.splitlines() if ln.strip() and not ln.startswith("-")), "")
        plan.judgment.append(Decision(
            kind="skill-disposition",
            subject=f".claude/skills/{name}",
            question=f"Does `{name}` hold a real repeatable procedure, or is it fieldbook bookkeeping?",
            evidence=[
                f"SKILL.md is {size:,} bytes",
                f"first line: {first_line[:100]}" if first_line else "no readable heading",
            ],
            options=[
                f"PORT  — move to .agents/skills/{name}/ and symlink (it is a real procedure)",
                "DROP  — quarantine it (it exists to maintain the memory system itself)",
            ],
        ))

    for rel in m.unscoped_rules:
        rule = root / rel
        text = rule.read_text(encoding="utf-8", errors="replace")
        headings = [ln.strip("# ").strip() for ln in text.splitlines() if ln.startswith("##")]
        plan.judgment.append(Decision(
            kind="rule-scoping",
            subject=rel,
            question=(
                f"{rule.stat().st_size:,} bytes load at launch in EVERY session. Which parts are "
                "path-scopable, which are genuinely universal, and which are dead?"
            ),
            evidence=[
                f"{rule.stat().st_size:,} bytes / {len(text.splitlines())} lines, always-loaded",
                f"{len(headings)} sections: {', '.join(headings[:8])}" if headings else "no ## sections",
            ],
            options=[
                "SPLIT — sections that only matter under certain paths get their own file with "
                "`paths:` frontmatter, and load only when a matching file is read",
                "PROMOTE — genuinely universal rules move into AGENTS.md, where all three "
                "harnesses read them rather than only Claude Code",
                "DROP — anything an agent can derive from the code is dead weight",
            ],
        ))

    for marker in m.fieldbook_dirs:
        d = root / marker
        files = [p for p in d.rglob("*") if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        plan.judgment.append(Decision(
            kind="fieldbook-content",
            subject=marker,
            question=(
                "What in here is durable project truth, and what is process exhaust? Only truth "
                "moves into docs/project/; the rest stays in git history."
            ),
            evidence=[
                f"{len(files):,} files, {total / 1_048_576:.1f} MB",
                "candidate destinations: SPEC.md (locked constraints), PLAN.md (active work only), "
                "STATUS.md (current state), decisions/ (one per decision), pitfalls.md (verified "
                "failures, capped at 100 lines)",
            ],
            options=[
                "TRIAGE — extract only what is still true into the canonical files, then "
                "quarantine the directory",
                "KEEP-READONLY — leave it in place as archive and never read it at startup "
                "(costs nothing; it is not loaded)",
            ],
        ))

    _, m2 = detect(root)
    if m2.policy_empty:
        plan.judgment.append(Decision(
            kind="policy",
            subject=".agents/compatibility.json policy",
            question=(
                "The gates are installed and enforce nothing repo-specific. What must actually "
                "be denied here?"
            ),
            evidence=[
                "denyWritePaths, denyMcpTools and measureOnWrite are all empty",
                "run `agentkit policy scaffold` to harvest a draft from this repo's existing "
                "gate scripts and AGENTS.md rather than writing one from scratch",
            ],
            options=[
                "SCAFFOLD — generate a draft from what the repo already enforces, then review it",
                "NONE — genuinely nothing repo-specific to deny (rare; say so in the declaration)",
            ],
        ))

    return plan


# ── rendering ─────────────────────────────────────────────────────────────────────────

def render(plan: Plan) -> str:
    lines = [
        f"# Migration plan — {Path(plan.repo).name}",
        "",
        f"Stage: **{plan.stage}**",
        f"Generated by `agentkit migrate`. Mechanical actions are applied with `--apply`;",
        "judgment calls are yours and are written out rather than guessed.",
        "",
    ]

    lines += [f"## Mechanical — {len(plan.mechanical)} action(s), applied with `--apply`", ""]
    if plan.mechanical:
        for a in plan.mechanical:
            lines += [f"- **{a.kind}** `{a.target}`", f"  - {a.detail}", f"  - reversible: {a.reversible}"]
    else:
        lines.append("- nothing mechanical outstanding.")
    lines.append("")

    lines += [f"## Judgment — {len(plan.judgment)} decision(s), NOT automated", ""]
    if plan.judgment:
        for i, d in enumerate(plan.judgment, 1):
            lines += [f"### {i}. {d.subject}", "", f"**{d.question}**", ""]
            for e in d.evidence:
                lines.append(f"- {e}")
            lines.append("")
            for o in d.options:
                lines.append(f"- [ ] {o}")
            lines.append("")
    else:
        lines.append("- nothing outstanding. This repo is fully migrated.")

    lines += [
        "",
        "---",
        "",
        "Nothing here is deleted. Mechanical actions quarantine into `.agents/quarantine/<stamp>/`",
        "with a manifest; `agentkit revert` restores them.",
        "",
    ]
    return "\n".join(lines)


# ── execution ─────────────────────────────────────────────────────────────────────────

def apply_mechanical(root: Path, plan: Plan, kit_root: Path) -> list[str]:
    """Perform only the mechanical actions. Returns a log of what happened."""
    log: list[str] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantine = _quarantine_dir(root, stamp)
    manifest: list[dict] = []

    for action in plan.mechanical:
        if action.kind == "neutralise-nested-agents":
            src = root / action.target
            if not src.exists():
                continue
            dst = src.with_name("AGENTS.archived.md")
            src.rename(dst)
            manifest.append({"op": "rename", "from": str(dst.relative_to(root)),
                             "to": action.target})
            log.append(f"renamed {action.target} -> {dst.relative_to(root)}")

        elif action.kind == "create-canonical-skeleton":
            project = root / "docs" / "project"
            project.mkdir(parents=True, exist_ok=True)
            (project / "decisions").mkdir(exist_ok=True)
            created = []
            for name in CANONICAL_FILES:
                target = project / name
                if target.exists():
                    continue
                template = kit_root / "templates" / f"{name.lower().replace('.md', '')}.md.tmpl"
                if name == "pitfalls.md" and template.exists():
                    target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
                else:
                    target.write_text(
                        f"# {name.replace('.md', '')}\n\n"
                        f"<!-- Created empty by `agentkit migrate`. Filling it is a judgment call:\n"
                        f"     see the migration plan. An empty canonical file is honest; a\n"
                        f"     generated one would be truth nobody wrote. -->\n",
                        encoding="utf-8")
                created.append(name)
                manifest.append({"op": "created", "path": str(target.relative_to(root))})
            log.append(f"created docs/project/ skeleton ({', '.join(created) or 'already present'})")

        elif action.kind == "declare-harness-written":
            compat_path = root / ".agents" / "compatibility.json"
            try:
                compat = json.loads(compat_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            declared = compat.setdefault("harnessWritten", [])
            if action.target not in declared:
                declared.append(action.target)
                compat_path.write_text(json.dumps(compat, indent=2) + "\n", encoding="utf-8")
                manifest.append({"op": "declared", "path": action.target})
                log.append(f"declared {action.target} as harness-written")

    if manifest:
        quarantine.mkdir(parents=True, exist_ok=True)
        (quarantine / "manifest.json").write_text(
            json.dumps({"stamp": stamp, "actions": manifest}, indent=2) + "\n", encoding="utf-8")
        log.append(f"manifest written to .agents/quarantine/{stamp}/manifest.json")
    return log


def quarantine_path(root: Path, rel: str, stamp: str) -> Path:
    """Move a path into quarantine, preserving its relative location."""
    src = root / rel
    dst = _quarantine_dir(root, stamp) / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return dst
