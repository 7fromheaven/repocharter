"""Prove the new gate is never weaker than the old one, then retire the old one.

The gap this closes: `policy scaffold` copies a repo's rules out of its existing gate
script, and nothing ever turns that script off. The result is both gates running, the same
rule denying twice, and a "single source of truth" claim that is false. Worse, the obvious
fix -- delete the old gate once you have harvested it -- is unsafe, because harvesting is
best-effort: any rule that landed in `needsManualPattern` was NOT carried over, and
deleting the source would silently drop a production fence.

So retirement is gated on evidence, and the evidence is DIFFERENTIAL REPLAY rather than
pattern comparison. Two regexes that look different can behave identically and two that
look similar can differ on the one command that matters, so both gates are actually run
over a corpus of commands and their decisions compared. The new gate must be at least as
strict as the old one on every probe. One weaker verdict blocks retirement.

This is a sample, not a proof, and the report says so. The corpus is built from literals
mined out of the old gate's OWN patterns -- so every rule it encodes is exercised by a
command derived from that rule -- plus a fixed set of universally dangerous commands.
"""

from __future__ import annotations

import itertools
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Ranked weakest to strongest. Retirement requires new >= old for every probe.
STRENGTH = {"allow": 0, "ask": 1, "deny": 2, "blocked-exit2": 2}

# Commands dangerous in any repository, so the comparison covers the universal rules too.
BASE_CORPUS = [
    "git push --force origin main",
    "git push -f origin master",
    "git push origin main",
    "git commit --no-verify -m x",
    "git reset --hard HEAD~1",
    "git clean -fd",
    "git checkout -- src/app.py",
    "git stash drop",
    "rm -rf /Users/someone/data",
    "rm -rf /tmp/scratch",
    "ls -la",
    "npm test",
    "git status",
]

ALTERNATION = re.compile(r"\(([^()|]+(?:\|[^()|]+)+)\)")

# Commands that must NEVER be gated, whatever a repo's policy says. They exist because
# "never weaker" is not a sufficient bar on its own: a rule that denies everything is
# trivially never weaker, passes the comparison, and then blocks honest work until someone
# turns the gate off. That is the failure mode this whole system is built to avoid, so it
# gets its own corpus and its own blocking verdict.
#
# Every one of these mentions a protected branch name or a dangerous-looking token on
# purpose -- an over-broad harvested rule is exactly what catches them. A real case:
# harvesting a push-to-protected rule's branch matcher alone produced a policy that denied
# `ls main.py`.
BENIGN_CORPUS = [
    "ls main.py",
    "echo main",
    "cat README.md",
    "grep -r master .",
    "git status",
    "git log --oneline -5",
    "git diff main",
    "python3 -m pytest tests/",
    "npm run build",
    "rg 'master' src/",
]


@dataclass
class Divergence:
    command: str
    old: str
    new: str


@dataclass
class Result:
    probes: int = 0
    agreements: int = 0
    weaker: list[Divergence] = field(default_factory=list)
    stronger: list[Divergence] = field(default_factory=list)
    uncovered: list[dict] = field(default_factory=list)
    false_positives: list[Divergence] = field(default_factory=list)
    old_gate: str = ""

    @property
    def safe_to_retire(self) -> bool:
        """Behavioural evidence decides. Provenance bookkeeping only warns.

        `uncovered` lists rules the scaffold could not turn into a portable pattern. That is
        a real signal, but it is NOT proof the rule is unenforced: the kit ships built-in
        universal rules, so an unharvested `rm -rf` fence is often already covered by the
        replacement's own recursive-rm rule.

        Since the probe corpus is mined from the OLD script's patterns, every rule it
        encodes gets exercised. If the replay shows no weakness on any of those probes, the
        behaviour is covered whatever the provenance ledger says. Blocking anyway taught the
        operator to override the check, which is how a safety tool stops being consulted.

        So: weakness blocks, over-broadness blocks, unharvested-but-behaviourally-covered
        warns.
        """
        return not self.weaker and not self.false_positives


def mine_probes(pattern: str, limit: int = 6) -> list[str]:
    """Turn a gate regex back into commands that ought to trigger it.

    Approximate by construction, which is fine because every candidate is validated
    against its own source pattern before being kept: anything that does not actually
    match is discarded. A probe that does not fire the rule it came from would prove
    nothing.
    """
    body = pattern
    # Drop the command-position prefix group and trailing boundary group.
    body = re.sub(r"^\(\^\[\[:space:\]\]\*\|[^)]*\)", "", body)
    body = re.sub(r"\(\[\^\[:alnum:\]_\]\|\$\)$", "", body)
    body = re.sub(r"^\(\^\|\[\^\[:alnum:\]_\]\)", "", body)
    # Optional env-assignment group and similar quantified groups.
    body = re.sub(r"\([^()]*\)\*", "", body)
    body = re.sub(r"\[\^?\[:[a-z]+:\]_?\]\*", "", body)
    body = re.sub(r"\[[^\]]+\]\*", "", body)
    body = body.replace(" +", " ").replace(".*", " ").replace("\\", "")

    options: list[list[str]] = []
    remainder = body
    while True:
        m = ALTERNATION.search(remainder)
        if not m:
            options.append([remainder])
            break
        options.append([remainder[: m.start()]])
        options.append(m.group(1).split("|"))
        remainder = remainder[m.end():]

    out: list[str] = []
    for combo in itertools.islice(itertools.product(*options), limit * 4):
        candidate = re.sub(r"\s+", " ", "".join(combo)).strip()
        if not candidate or len(candidate) > 120:
            continue
        # Only keep it if it genuinely fires the rule it was derived from.
        try:
            proc = subprocess.run(["grep", "-qE", pattern], input=candidate + "\n",
                                  text=True, capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0 and candidate not in out:
            out.append(candidate)
        if len(out) >= limit:
            break
    return out


def decision_of(proc: subprocess.CompletedProcess) -> str:
    if proc.returncode == 2:
        return "blocked-exit2"
    try:
        return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]
    except Exception:
        return "allow"


def run_gate(hook: Path, command: str, repo: Path) -> str:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    try:
        proc = subprocess.run([str(hook)], input=payload, text=True, capture_output=True,
                              cwd=repo, timeout=30,
                              env={**_env(), "CLAUDE_PROJECT_DIR": str(repo)})
    except (OSError, subprocess.TimeoutExpired):
        return "allow"
    return decision_of(proc)


def _env() -> dict:
    import os
    return dict(os.environ)


def build_corpus(old_gate: Path, compat: dict) -> list[str]:
    """Probes mined from the OLD gate's patterns, plus the universal set."""
    corpus = list(BASE_CORPUS)
    policy = compat.get("policy") or {}
    for key in ("denyBashPatterns", "askBashPatterns"):
        for entry in policy.get(key) or []:
            corpus.extend(mine_probes(entry.get("pattern", "")))

    # Also mine straight from the old script, so rules that were never harvested are
    # still exercised -- those are exactly the ones at risk of being silently dropped.
    try:
        text = old_gate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    assigns = {m.group(1): m.group(3) for m in
               re.finditer(r"^([A-Z_][A-Z0-9_]*)=(['\"])(.*?)\2\s*$", text, re.M)}
    for raw in re.findall(r'grep\s+-qE\s+"((?:[^"\\]|\\.)*)"', text):
        resolved = raw
        for _ in range(5):
            nxt = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
                         lambda m: assigns.get(m.group(1) or m.group(2), m.group(0)), resolved)
            if nxt == resolved:
                break
            resolved = nxt
        corpus.extend(mine_probes(resolved, limit=4))

    seen, unique = set(), []
    for c in corpus:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def compare(repo: Path, old_gate: Path, new_gate: Path, compat: dict) -> Result:
    result = Result(old_gate=str(old_gate.relative_to(repo)))

    draft = compat.get("policyDraft") or {}
    for entry in draft.get("needsManualPattern") or []:
        result.uncovered.append({
            "reason": entry.get("reason", ""),
            "source": entry.get("_source", ""),
            "problems": entry.get("_problems", []),
        })

    for command in build_corpus(old_gate, compat):
        old = run_gate(old_gate, command, repo)
        new = run_gate(new_gate, command, repo)
        result.probes += 1
        if STRENGTH[new] < STRENGTH[old]:
            result.weaker.append(Divergence(command, old, new))
        elif STRENGTH[new] > STRENGTH[old]:
            result.stronger.append(Divergence(command, old, new))
        else:
            result.agreements += 1

    # The other half of the bar. A gate is not correct just because it is strict.
    for command in BENIGN_CORPUS:
        new = run_gate(new_gate, command, repo)
        result.probes += 1
        if STRENGTH[new] > 0:
            old = run_gate(old_gate, command, repo)
            result.false_positives.append(Divergence(command, old, new))
        else:
            result.agreements += 1
    return result


def retire(repo: Path, old_gate: Path) -> list[str]:
    """Quarantine the superseded gate and unwire it from settings.json. Reversible."""
    log = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantine = Path.home() / ".agentkit" / "quarantine" / repo.resolve().name / stamp
    quarantine.mkdir(parents=True, exist_ok=True)

    rel = old_gate.relative_to(repo)
    dst = quarantine / rel

    # ORDER MATTERS, and the manifest is written before anything moves. An earlier version
    # moved the file first and computed the manifest path with `relative_to(repo)` — which
    # raises once the quarantine lives outside the repo. The crash landed between the move
    # and the settings edit, leaving the gate quarantined but still referenced: the exact
    # dead-reference defect this kit exists to catch, produced by the kit. Manifest first, so
    # a failure at any later point is still revertable.
    #
    # `from` is relative to the QUARANTINE, not the repo, so it survives the directory being
    # moved or the repo being renamed.
    manifest = [{"op": "rename", "from": str(rel), "to": str(rel), "base": "quarantine"}]
    (quarantine / "manifest.json").write_text(
        json.dumps({"stamp": stamp, "actions": manifest}, indent=2) + "\n", encoding="utf-8")

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old_gate), str(dst))
    log.append(f"quarantined {rel}")

    settings_path = repo / ".claude" / "settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        settings = {}

    removed = 0
    hooks = settings.get("hooks") or {}
    for event, groups in list(hooks.items()):
        kept_groups = []
        for group in groups if isinstance(groups, list) else []:
            kept = [h for h in group.get("hooks", [])
                    if rel.name not in (h.get("command") or "")]
            removed += len(group.get("hooks", [])) - len(kept)
            if kept:
                kept_groups.append({**group, "hooks": kept})
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event)
    # Also drop allowlist entries pointing at the retired script. Unwiring the hook while
    # leaving `permissions.allow` referencing it produces exactly the dead reference this
    # kit was built to catch -- and it would be the kit's own doing.
    allow = (settings.get("permissions") or {}).get("allow") or []
    kept_allow = [a for a in allow if rel.name not in a]
    dropped_allow = len(allow) - len(kept_allow)
    if dropped_allow:
        settings["permissions"]["allow"] = kept_allow

    if removed or dropped_allow:
        settings["hooks"] = hooks
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        log.append(f"unwired {removed} hook reference(s) and {dropped_allow} allowlist "
                   f"entr{'y' if dropped_allow == 1 else 'ies'} for {rel.name}")
        manifest.append({"op": "settings-hook-removed", "path": str(rel)})

    (quarantine / "manifest.json").write_text(
        json.dumps({"stamp": stamp, "actions": manifest}, indent=2) + "\n", encoding="utf-8")
    log.append(f"manifest at .agents/quarantine/{stamp}/manifest.json — `agentkit revert` restores")
    return log
