"""Migration stage detection.

The problem this solves: `verify` on a repo that has not been migrated yet reports nine
errors, and so does a repo that is genuinely broken. Across ten repositories that is ninety
undifferentiated errors with no way to tell progress from failure, which is the same as
having no signal at all.

So findings are split by whether they depend on migration progress:

  BLOCKING   wrong at every stage. A dead hook reference, an AGENTS.override.md, an
             absolute symlink, a file over the Codex cap. These are errors the moment they
             appear and they never become acceptable.

  MIGRATION  expected until the migration finishes. A fieldbook directory, a retired skill,
             an unscoped rules file, a missing docs/project. These are TODO items while the
             repo is mid-migration and only become errors once it claims to be done.

A repo's stage is derived from the filesystem rather than declared, because a declared
stage is a thing that goes stale silently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

NOT_STARTED = "not-started"
MECHANICAL = "mechanical"
MIGRATED = "migrated"

STAGE_ORDER = [NOT_STARTED, MECHANICAL, MIGRATED]

STAGE_HELP = {
    NOT_STARTED: "no agentkit layer yet — run `agentkit apply`",
    MECHANICAL: "plumbing installed, migration incomplete — run `agentkit migrate`",
    MIGRATED: "canonical layout, no fieldbook residue",
}

# Directories that mean a ctx-fieldbook (or similar) system is still live.
FIELDBOOK_MARKERS = (".agent-docs", ".claude/handoffs")

RETIRED_SKILLS = {
    "checkpoint", "handoff", "lessons", "plan-sync", "state-router",
    "dispatch-gate", "frontmatter-lint", "id-spine", "log-lesson",
    "distill-lessons", "flush", "orient",
}


@dataclass
class Markers:
    """Everything stage detection looked at, kept so the tool can explain itself."""
    has_compat: bool = False
    has_project_dir: bool = False
    fieldbook_dirs: list[str] = field(default_factory=list)
    retired_skills: list[str] = field(default_factory=list)
    unscoped_rules: list[str] = field(default_factory=list)
    nested_agents: list[str] = field(default_factory=list)
    policy_empty: bool = True
    policy_draft_pending: bool = False

    @property
    def residue(self) -> bool:
        """True when the STRUCTURAL migration is unfinished.

        Deliberately excludes unscoped rules and nested AGENTS.md, even though both are
        migration work. They are CONTENT defects that a fully migrated repo can acquire at
        any time by committing one bad file — so if they counted as stage markers, adding a
        bad rule to a finished repo would silently demote it to `mechanical` and downgrade
        its own error into a todo. The defect would hide the alarm.

        Stage answers "has the structure been migrated". Findings answer "is anything
        wrong". Conflating them means a regression can never escalate.
        """
        return bool(
            self.fieldbook_dirs or self.retired_skills or not self.has_project_dir
        )


def _has_paths_frontmatter(text: str) -> bool:
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False
    return "paths:" in text[3:end]


def scan(root: Path) -> Markers:
    m = Markers()
    compat_path = root / ".agents" / "compatibility.json"
    m.has_compat = compat_path.exists()

    compat = {}
    if m.has_compat:
        try:
            compat = json.loads(compat_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            compat = {}

    project = root / (compat.get("canonical", {}).get("project") or "docs/project")
    m.has_project_dir = project.is_dir()

    for marker in FIELDBOOK_MARKERS:
        if (root / marker).is_dir():
            m.fieldbook_dirs.append(marker)

    skills_dir = root / ".claude" / "skills"
    if skills_dir.is_dir():
        for entry in sorted(skills_dir.iterdir()):
            if entry.name in RETIRED_SKILLS:
                m.retired_skills.append(entry.name)

    rules_dir = root / ".claude" / "rules"
    if rules_dir.is_dir():
        for rule in sorted(rules_dir.rglob("*.md")):
            text = rule.read_text(encoding="utf-8", errors="replace")
            if not _has_paths_frontmatter(text):
                m.unscoped_rules.append(str(rule.relative_to(root)))

    for nested in sorted(root.rglob("AGENTS.md")):
        if ".git" in nested.parts or "node_modules" in nested.parts:
            continue
        if nested.parent != root:
            m.nested_agents.append(str(nested.relative_to(root)))

    policy = compat.get("policy") or {}
    m.policy_empty = not any(
        policy.get(key) for key in
        ("denyBashPatterns", "askBashPatterns", "denyWritePaths", "denyMcpTools", "measureOnWrite")
    )
    m.policy_draft_pending = bool(compat.get("policyDraft"))

    return m


def detect(root: Path) -> tuple[str, Markers]:
    m = scan(root)
    if not m.has_compat:
        return NOT_STARTED, m
    if m.residue:
        return MECHANICAL, m
    return MIGRATED, m


def summarise(stage: str, m: Markers) -> list[str]:
    """One line per reason the stage is what it is."""
    out = []
    if not m.has_compat:
        out.append("no .agents/compatibility.json")
        return out
    if m.fieldbook_dirs:
        out.append(f"fieldbook still present: {', '.join(m.fieldbook_dirs)}")
    if m.retired_skills:
        out.append(f"{len(m.retired_skills)} retired skill(s): {', '.join(m.retired_skills)}")
    if m.unscoped_rules:
        out.append(f"{len(m.unscoped_rules)} unscoped rule file(s) — always-loaded")
    if m.nested_agents:
        out.append(f"{len(m.nested_agents)} nested AGENTS.md")
    if not m.has_project_dir:
        out.append("no docs/project/ — canonical durable state does not exist yet")
    if stage == MIGRATED and m.policy_empty:
        out.append("policy is empty — the gates are installed but enforce nothing repo-specific")
    return out
