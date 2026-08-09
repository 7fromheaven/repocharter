"""Build a reviewable policy draft from a supported repository configuration.

The importer reads ``block`` and ``ask`` decisions from compatible shell gates and surfaces
prose instructions as a checklist. It does not infer executable patterns from prose.

Imported rules are written to ``policyDraft`` rather than the active ``policy`` section.
Review and explicit promotion are required because translating shell control flow into a
portable policy can change rule semantics.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# Decision vocabulary used by supported legacy shell gates.
DECISION_CALL = re.compile(r'^\s*(block|ask)\s+"((?:[^"\\]|\\.)*)"', re.M)
GREP_PATTERN = re.compile(r'grep\s+-qE\s+"((?:[^"\\]|\\.)*)"')

# `VAR='...'` / `VAR="..."` at the start of a line — the shell variables a gate builds its
# patterns from, e.g. CSEP (the command-position anchor) and PROTECTED_BRANCHES.
SHELL_ASSIGN = re.compile(r"^([A-Z_][A-Z0-9_]*)=(['\"])(.*?)\2\s*$", re.M)
SHELL_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

# GNU-grep extensions. BSD/macOS grep does NOT support them, so a pattern carrying one
# behaves differently on the operator's machine than in CI — or matches nothing at all.
NON_PORTABLE = re.compile(r"\\[bswdBSWD]")


def _resolve_vars(pattern: str, assignments: dict[str, str], depth: int = 0) -> str:
    """Substitute the script's own shell variables into a harvested pattern.

    A pattern lifted out of a shell script can contain references such as ``${CSEP}`` that
    have meaning only inside that script. Resolve variables defined by the source and reject
    unresolved references before promotion.
    """
    if depth > 5:
        return pattern

    def sub(m: re.Match) -> str:
        name = m.group(1) or m.group(2)
        return assignments.get(name, m.group(0))

    resolved = SHELL_VAR.sub(sub, pattern)
    if resolved != pattern:
        return _resolve_vars(resolved, assignments, depth + 1)
    return resolved


WORDISH = re.compile(r"[A-Za-z0-9_)\]]")

# Exact POSIX equivalents for the GNU escapes. These are the substitutions the gate's own
# header prescribes ("Use [[:space:]] not \s, [^[:space:]] not \S").
CLASS_EQUIV = {
    r"\s": "[[:space:]]",
    r"\S": "[^[:space:]]",
    r"\w": "[[:alnum:]_]",
    r"\W": "[^[:alnum:]_]",
    r"\d": "[[:digit:]]",
    r"\D": "[^[:digit:]]",
}


def portable_boundaries(pattern: str) -> str:
    r"""Rewrite GNU-only escapes into POSIX so the pattern means the same everywhere.

    `\b \s \S \w \W \d \D` are not part of POSIX ERE and are unavailable in some grep
    implementations. Normalize them to explicit POSIX classes before validation.

    `\b` is context-dependent: a boundary after a word character is trailing, otherwise
    leading. Everything else is a straight class substitution.

    Any translation that produces an invalid pattern is caught by `grep_accepts` downstream
    and lands in needsManualPattern rather than shipping.
    """
    out, i = [], 0
    while i < len(pattern):
        two = pattern[i:i + 2]
        if two == r"\b":
            before = pattern[i - 1] if i else ""
            trailing = bool(before and WORDISH.match(before))
            out.append("([^[:alnum:]_]|$)" if trailing else "(^|[^[:alnum:]_])")
            i += 2
        elif two in CLASS_EQUIV:
            out.append(CLASS_EQUIV[two])
            i += 2
        else:
            out.append(pattern[i])
            i += 1
    return "".join(out)


def grep_accepts(pattern: str) -> tuple[bool, str]:
    """Ask grep -E itself whether the pattern is valid.

    Python's `re` is the wrong grammar to judge these by: it parses `[[:alnum:]_]` as a
    character set containing `[`, `:`, `a`... rather than a POSIX class, so it both accepts
    nonsense and warns about valid patterns. grep is what actually runs them, so grep is
    what gets to decide.
    """
    try:
        proc = subprocess.run(["grep", "-qE", pattern], input="", text=True,
                              capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return True, f"could not run grep to validate ({exc})"
    if proc.returncode >= 2:
        return False, (proc.stderr.strip().splitlines() or ["grep rejected the pattern"])[-1]
    return True, ""


# Commands that must never be gated. A harvested pattern matching any of these is
# over-broad and is refused rather than promoted.
#
# This complements the "never weaker" check. A rule that denies everything is trivially
# never weaker than the rule it replaces, but it is still invalid because it blocks benign
# commands.
BENIGN_PROBES = [
    "ls main.py", "echo main", "cat README.md", "grep -r master .",
    "git status", "git log --oneline -5", "git diff main",
    "python3 -m pytest tests/", "npm run build", "rg 'master' src/",
]


def over_broad(patterns: list[str]) -> list[str]:
    """Benign commands this rule would block. Empty means it is appropriately narrow."""
    hits = []
    for probe in BENIGN_PROBES:
        matched_all = True
        for pattern in patterns:
            try:
                proc = subprocess.run(["grep", "-qE", pattern], input=probe + "\n",
                                      text=True, capture_output=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                matched_all = False
                break
            if proc.returncode != 0:
                matched_all = False
                break
        if matched_all:
            hits.append(probe)
    return hits


def unsatisfiable(patterns: list[str]) -> bool:
    """True when no command can satisfy ALL of these patterns at once.

    `allOf` is a conjunction. A gate script that writes `if A || B || C; then block` states
    ALTERNATIVES, and harvesting those three into one allOf produces a rule that can only
    fire on a command matching all three simultaneously — which, for mutually exclusive
    alternatives, is never. The rule then sits in the policy looking exactly like a working
    fence.

    Detection is empirical rather than analytical: mine probes from each pattern, and if no
    probe drawn from any of them satisfies every pattern, treat the conjunction as
    unsatisfiable. It can miss an exotic case, but it catches the shape that actually occurs.
    """
    if len(patterns) < 2:
        return False
    candidates: list[str] = []
    for p in patterns:
        candidates.extend(_probe_strings(p))
    if not candidates:
        return False
    for cand in candidates:
        if all(_matches(p, cand) for p in patterns):
            return False
    return True


def _matches(pattern: str, text: str) -> bool:
    try:
        return subprocess.run(["grep", "-qE", pattern], input=text + "\n", text=True,
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _probe_strings(pattern: str, limit: int = 4) -> list[str]:
    """A few strings that satisfy `pattern`, built by stripping regex furniture."""
    body = pattern
    body = re.sub(r"^\(\^\[\[:space:\]\]\*\|[^)]*\)", "", body)
    body = re.sub(r"^\(\^\|\[\^\[:alnum:\]_\]\)", "", body)
    body = re.sub(r"\(\[\^\[:alnum:\]_\]\|\$\)$", "", body)
    body = re.sub(r"\([^()]*\)\*", "", body)
    body = re.sub(r"\[\^?\[:[a-z]+:\]_?\]\*", "", body)
    body = body.replace(" +", " ").replace(".*", " x ").replace("\\", "")
    out: list[str] = []
    alts = ALT_GROUP.search(body)
    branches = alts.group(1).split("|")[:limit] if alts else [""]
    for b in branches:
        cand = (body[:alts.start()] + b + body[alts.end():]) if alts else body
        cand = re.sub(r"[()\[\]^$?]", "", cand)
        cand = re.sub(r"\s+", " ", cand).strip()
        if cand and _matches(pattern, cand):
            out.append(cand)
    return out


ALT_GROUP = re.compile(r"\(([^()|]+(?:\|[^()|]+)+)\)")


def diagnose(pattern: str) -> list[str]:
    """Every reason this pattern would be inert or unportable. Empty means usable."""
    problems = []
    if not pattern.strip():
        problems.append("empty pattern")
        return problems
    if SHELL_VAR.search(pattern):
        unresolved = sorted({m.group(1) or m.group(2) for m in SHELL_VAR.finditer(pattern)})
        problems.append(
            f"unresolved shell variable(s) {', '.join(unresolved)} — as a literal string this "
            "matches nothing, silently"
        )
    if NON_PORTABLE.search(pattern):
        found = sorted(set(NON_PORTABLE.findall(pattern)))
        problems.append(
            f"GNU-only escape(s) {', '.join(found)} — BSD/macOS grep -E does not implement "
            "these; spell boundaries out as (^|[^[:alnum:]_]) / ([^[:alnum:]_]|$)"
        )
    ok, why = grep_accepts(pattern)
    if not ok:
        problems.append(f"grep -E rejects it: {why}")
    return problems

# Prose rules worth turning into something mechanical.
NEVER_LINE = re.compile(r"^\s*[-*]\s+(.*(?:never|do not|must not|no one may).*)$", re.I | re.M)



def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace('\\"', '"')).strip()


def from_hook_scripts(root: Path) -> list[dict]:
    """Extract each existing gate rule as a draft entry, reason first."""
    out: list[dict] = []
    hooks_dir = root / ".claude" / "hooks"
    if not hooks_dir.is_dir():
        return out

    for script in sorted(hooks_dir.rglob("*.sh")):
        if "/agentkit/" in str(script):
            continue  # ours; nothing to harvest
        try:
            text = script.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # The script's own variables, so ${CSEP} and friends resolve to what they mean here.
        assignments = {m.group(1): m.group(3) for m in SHELL_ASSIGN.finditer(text)}

        lines = text.splitlines()
        for match in DECISION_CALL.finditer(text):
            verb, reason = match.group(1), _clean(match.group(2))
            line_no = text[: match.start()].count("\n")
            # The patterns that guard this decision are the few lines above it.
            window = "\n".join(lines[max(0, line_no - 6): line_no])
            raw = [_clean(p) for p in GREP_PATTERN.findall(window)]
            resolved = [_resolve_vars(p, assignments) for p in raw]

            # Preserve the source control flow. A gate writes either
            #   grep A && grep B && block   (conjunction — both must hold)
            # or
            #   grep A || grep B; then block  (alternatives — either suffices)
            # and reading the second as the first produces a rule that cannot fire.
            joiner = "or" if ("||" in window and "&&" not in window) else \
                     ("mixed" if ("||" in window and "&&" in window) else "and")
            out.append({
                "source": f"{script.relative_to(root)}:{line_no + 1}",
                "verb": verb,
                "reason": reason[:300],
                "patterns": resolved[:3],
                "rawPatterns": raw[:3],
                "joiner": joiner,
            })
    return out


def from_agents_md(root: Path) -> list[dict]:
    """Return prose rules as a checklist without inferring executable patterns.

    Natural-language instructions do not reliably identify command regexes, tool names, or
    path globs. Each item therefore retains its source text for human classification and no
    enforcement fields are synthesized.
    """
    agents = root / "AGENTS.md"
    if not agents.exists():
        return []
    text = agents.read_text(encoding="utf-8", errors="replace")
    out = []
    for match in NEVER_LINE.finditer(text):
        rule = _clean(match.group(1))
        if len(rule) < 12:
            continue
        out.append({"source": "AGENTS.md", "rule": rule[:300]})
    return out


def protected_branches(root: Path) -> list[str]:
    """Recover protected branches from the source gate's shell assignment.

    Reading the source list prevents migration from narrowing protection to a manually
    supplied subset.
    """
    hooks_dir = root / ".claude" / "hooks"
    if not hooks_dir.is_dir():
        return []
    found: list[str] = []
    for script in sorted(hooks_dir.rglob("*.sh")):
        if "/agentkit/" in str(script):
            continue
        try:
            text = script.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r"^(?:PROTECTED|PROTECTED_BRANCHES)=(['\"])(.*?)\1\s*$", text, re.M):
            raw = m.group(2)
            # Strip regex furniture BEFORE extracting names. `\bmain\b|\bmaster\b` otherwise
            # yields "bmain" and "bmaster" — the boundary escape absorbed into the branch.
            raw = re.sub(r"\\[a-zA-Z]", "|", raw)          # \b \s ... become separators
            raw = re.sub(r"[\[\]()^$*+?{}.]", "|", raw)    # and so does POSIX-class furniture
            for name in re.split(r"[|\\/,\s]+", raw):
                name = name.strip()
                if name and re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]*", name) and name not in found:
                    found.append(name)
    return found


def build_draft(root: Path) -> dict:
    """Assemble the draft. Every entry carries where it came from and needs review."""
    harvested = from_hook_scripts(root)
    prose = from_agents_md(root)

    deny_bash, ask_bash, unusable = [], [], []
    for item in harvested:
        if not item["patterns"]:
            continue
        # A gate that chains `grep A && grep B && block` means A AND B. Taking only the
        # first changes the rule in both directions. Conjunctions are preserved as allOf.
        raw_patterns = [p for p in item["patterns"] if p.strip()]
        translated_all = [portable_boundaries(p) for p in raw_patterns]
        was_translated = translated_all != raw_patterns
        pattern = translated_all[0]
        problems = [p for pat in translated_all for p in diagnose(pat)]
        blocked_benign = over_broad(translated_all) if not problems else []
        if blocked_benign:
            problems.append(
                "OVER-BROAD: this rule would block " + ", ".join(repr(b) for b in blocked_benign[:4])
                + ". Usually a conjunction that lost one of its halves — check whether the source "
                "chained this grep with another one that was not captured."
            )
        if problems:
            # NEVER emit an inert pattern. A rule that silently matches nothing is worse
            # than no rule: it occupies the slot where a real one would go and reads as
            # protection on every subsequent review.
            unusable.append({
                "_source": item["source"],
                "_problems": problems,
                "verb": item["verb"],
                "reason": item["reason"],
                "harvestedPattern": pattern,
                "originalPattern": item["rawPatterns"][0] if item.get("rawPatterns") else pattern,
            })
            continue
        entry = {
            "_source": item["source"],
            "_review": "NEEDS REVIEW — harvested from an existing gate; confirm it still means what it did",
            "reason": item["reason"],
        }
        if len(translated_all) > 1 and item.get("joiner") == "and":
            entry["allOf"] = translated_all
            entry["_conjunction"] = (
                f"the source chained {len(translated_all)} greps with &&, so ALL must match. "
                "Harvesting only one of them would change what the rule denies."
            )
        elif len(translated_all) > 1:
            # Alternatives (or an ambiguous mix). Each becomes its own rule: correct for a
            # real `||`, and strictly-more-denial for a mix — with over_broad above already
            # having rejected any single pattern that would block ordinary work.
            entry["_split"] = (
                f"the source joined {len(translated_all)} greps with `||` "
                f"({item.get('joiner')}), so they are ALTERNATIVES. Emitted as separate rules; "
                "a conjunction here could never fire."
            )
            for extra in translated_all[1:]:
                sibling = dict(entry)
                sibling["pattern"] = extra
                (deny_bash if item["verb"] == "block" else ask_bash).append(sibling)
            entry["pattern"] = translated_all[0]
        else:
            entry["pattern"] = pattern
        if was_translated:
            entry["_translated"] = (
                r"GNU \b boundaries were rewritten to the POSIX spelling so this behaves the "
                "same under BSD/macOS grep as under GNU. Re-read it: the original is at "
                + item["source"]
            )
        if item["verb"] == "block":
            deny_bash.append(entry)
        else:
            entry["_review"] = "NEEDS REVIEW — this was an `ask`, not a `block`. Decide which it should be."
            ask_bash.append(entry)

    # Prose is a checklist, never an inferred rule. See from_agents_md for why.
    write_paths, mcp_tools = [], []
    unclassified = [{"_source": item["source"], "rule": item["rule"]} for item in prose]

    return {
        "_generated": "agentkit policy scaffold",
        "_contract": (
            "NOTHING HERE IS ENFORCED. Move entries into `policy` (dropping the _source/_review "
            "keys) to turn them on. Review every imported regex against the source control flow "
            "before promotion."
        ),
        "protectedBranches": protected_branches(root),
        "denyBashPatterns": deny_bash,
        "askBashPatterns": ask_bash,
        "denyWritePaths": write_paths,
        "denyMcpTools": mcp_tools,
        "unclassifiedProseRules": unclassified,
        "needsManualPattern": unusable,
    }


def write_draft(root: Path, draft: dict) -> tuple[int, Path]:
    """Store the draft in compatibility.json under `policyDraft`. Returns (count, path)."""
    path = root / ".agents" / "compatibility.json"
    compat = json.loads(path.read_text(encoding="utf-8"))
    compat["policyDraft"] = draft
    path.write_text(json.dumps(compat, indent=2) + "\n", encoding="utf-8")
    count = sum(len(draft[k]) for k in
                ("denyBashPatterns", "askBashPatterns", "denyWritePaths", "denyMcpTools"))
    return count, path


def lint_policy(policy: dict) -> list[str]:
    """Every reason a live policy pattern would be inert. Used by `verify`.

    This is the backstop. The scaffold refuses to emit a broken pattern, but a pattern can
    also be hand-written, pasted, or edited later — and an inert deny rule reads exactly
    like a working one from the outside.
    """
    problems = []
    for key in ("denyBashPatterns", "askBashPatterns"):
        for entry in policy.get(key) or []:
            # A rule carries EITHER `pattern` or `allOf`. Reading only `pattern` reports a
            # perfectly good conjunction as an empty, inert rule.
            patterns = entry.get("allOf") or ([entry["pattern"]] if entry.get("pattern") else [])
            if not patterns:
                problems.append(f"policy.{key}: a rule has neither `pattern` nor `allOf`")
                continue
            for pattern in patterns:
                for problem in diagnose(pattern):
                    problems.append(f"policy.{key} {pattern!r}: {problem}")
            if unsatisfiable(patterns):
                problems.append(
                    f"policy.{key} allOf with {len(patterns)} patterns CAN NEVER FIRE — no command "
                    f"satisfies all of them at once. This is the shape a harvested `||` chain takes: "
                    f"the source stated ALTERNATIVES and they became a conjunction. Split it into "
                    f"one entry per pattern. Reason given: {entry.get('reason', '')[:70]}"
                )
    for entry in policy.get("denyMcpTools") or []:
        try:
            re.compile(entry.get("pattern", ""))
        except re.error as exc:
            problems.append(f"policy.denyMcpTools {entry.get('pattern', '')!r}: invalid regex ({exc})")
    return problems
