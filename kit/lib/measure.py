"""Exercise every declared policy rule against the installed gate.

Configuration alone cannot establish that a rule is active. For each rule, this module
constructs a probe that matches the declared pattern, validates it with ``grep -E``, runs
it through the installed gate, and compares the observed decision with the declaration.

Two inconclusive outcomes are reported separately:

  INERT       every sub-pattern yields probes on its own, yet no combination of
              them satisfies all sub-patterns at once. The conditions contradict.
              This is the shape produced when alternatives are represented as a
              conjunction.

  UNPROBEABLE the miner could not build a matching probe at all. That is a
              statement about the miner, not about the rule. Reported separately
              and never counted as a broken rule.

A rule this command cannot measure is reported as unmeasured. It is never reported
as passing.
"""

from __future__ import annotations

import fnmatch
import itertools
import json
import re
import subprocess
from pathlib import Path

from supersede import (  # noqa: E402  — lib/ is on sys.path as a flat module dir
    BASE_CORPUS,
    BENIGN_CORPUS,
    STRENGTH,
    decision_of,
    mine_probes,
    run_gate,
    _env,
)

# Verdicts, worst first — the order they are reported in.
INERT = "INERT"
UNENFORCED = "UNENFORCED"
WEAKER = "WEAKER"
OVERBROAD = "OVERBROAD"
NARROW = "NARROW"
UNPROBEABLE = "UNPROBEABLE"
ENFORCED = "ENFORCED"

BROKEN = (INERT, UNENFORCED, WEAKER, OVERBROAD, NARROW)

# Cap the composition search. allOf rules in practice carry 2-4 patterns; the
# product of 6 probes each is already 1296 candidates before permutation, and a
# contradiction shows itself long before that.
_MAX_COMPOSITES = 400


# ── ERE expansion ─────────────────────────────────────────────────────────────
#
# `mine_probes` strips a pattern down with substitutions. That is insufficient for
# nested optional groups like
# `(npx +|pnpm +(exec +|dlx +))?`, negated classes like `[^[:alnum:]_$]`, escaped
# literals like `\$\{?`, and similar production policy patterns.
#
# This bounded recursive-descent expander covers the ERE subset used by those patterns.
# Every generated string is validated with `grep -E` against the source pattern, so an
# unsupported construct yields UNPROBEABLE rather than a false pass.

_POSIX_REP = {
    "alnum": "x", "alpha": "x", "digit": "1", "lower": "x", "upper": "X",
    "space": " ", "punct": "-", "word": "x", "xdigit": "a", "blank": " ",
    "print": "x", "graph": "x", "cntrl": "\t",
}
_NEG_FALLBACKS = ("x", " ", "/", "1", "-", "=")
_MAX_BRANCH = 6


def _cap(items: list[str]) -> list[str]:
    out: list[str] = []
    for i in items:
        if i not in out:
            out.append(i)
        if len(out) >= _MAX_BRANCH:
            break
    return out


class _EREExpander:
    """Yield concrete strings that ought to match an ERE. Bounded, approximate."""

    def __init__(self, pattern: str):
        self.s = pattern
        self.i = 0

    def peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def expand(self) -> list[str]:
        return _cap(self._alt())

    def _alt(self) -> list[str]:
        branches = [self._concat()]
        while self.peek() == "|":
            self.i += 1
            branches.append(self._concat())
        out: list[str] = []
        for b in branches:
            out.extend(b)
        return _cap(out)

    def _concat(self) -> list[str]:
        parts: list[list[str]] = []
        while self.peek() and self.peek() not in "|)":
            parts.append(self._repeat())
        if not parts:
            return [""]
        out = [""]
        for part in parts:
            nxt = [a + b for a in out for b in part]
            out = _cap(nxt)
        return out

    def _repeat(self) -> list[str]:
        atom = self._atom()
        q = self.peek()
        if q in "*?":
            self.i += 1
            # Both skipping and taking it once — an optional group is exactly where
            # a probe can go wrong, so try it present and absent.
            return _cap([""] + atom)
        if q == "+":
            self.i += 1
            return atom
        if q == "{":
            close = self.s.find("}", self.i)
            if close != -1:
                spec = self.s[self.i + 1:close]
                self.i = close + 1
                try:
                    n = int(spec.split(",")[0] or 1)
                except ValueError:
                    n = 1
                return _cap([a * max(n, 1) for a in atom])
        return atom

    def _atom(self) -> list[str]:
        c = self.peek()
        if c == "(":
            self.i += 1
            inner = self._alt()
            if self.peek() == ")":
                self.i += 1
            return inner
        if c == "[":
            return self._bracket()
        if c == "\\":
            self.i += 2
            return [self.s[self.i - 1]] if self.i - 1 < len(self.s) else [""]
        if c in "^$":
            self.i += 1
            return [""]          # an anchor contributes no characters
        if c == ".":
            self.i += 1
            return ["x"]
        self.i += 1
        return [c]

    def _bracket(self) -> list[str]:
        self.i += 1                      # consume '['
        negated = False
        if self.peek() == "^":
            negated = True
            self.i += 1
        members: list[str] = []
        classes: list[str] = []
        first = True
        while self.i < len(self.s):
            if self.peek() == "]" and not first:
                self.i += 1
                break
            first = False
            if self.s.startswith("[:", self.i):
                end = self.s.find(":]", self.i)
                if end == -1:
                    self.i += 1
                    continue
                classes.append(self.s[self.i + 2:end])
                self.i = end + 2
                continue
            ch = self.s[self.i]
            if ch == "\\" and self.i + 1 < len(self.s):
                members.append(self.s[self.i + 1])
                self.i += 2
                continue
            # a range a-z contributes its low end
            if (self.i + 2 < len(self.s) and self.s[self.i + 1] == "-"
                    and self.s[self.i + 2] != "]"):
                members.append(ch)
                self.i += 3
                continue
            members.append(ch)
            self.i += 1

        if not negated:
            for cls in classes:
                members.append(_POSIX_REP.get(cls, "x"))
            return _cap([m for m in members if m] or ["x"])

        # Negated: pick something the class demonstrably excludes.
        excluded = set(members)
        for cls in classes:
            excluded.add(_POSIX_REP.get(cls, "x"))
            if cls in ("alnum", "alpha", "word", "lower", "print", "graph"):
                excluded.update("xX")
            if cls in ("digit", "xdigit", "alnum", "word", "print", "graph"):
                excluded.update("1a")
            if cls in ("space", "blank"):
                excluded.add(" ")
        for cand in _NEG_FALLBACKS:
            if cand not in excluded:
                return [cand]
        return ["~"]


def expand_ere(pattern: str, limit: int = 6) -> list[str]:
    """Concrete strings that ought to match `pattern`, validated by grep -E."""
    try:
        raw = _EREExpander(pattern).expand()
    except (IndexError, RecursionError, ValueError):
        return []
    out: list[str] = []
    for cand in raw:
        cand = cand.strip()
        if not cand or len(cand) > 200 or cand in out:
            continue
        if _matches_all(cand, [pattern]):
            out.append(cand)
        if len(out) >= limit:
            break
    return out


# Concrete commands for common policy shapes. These are tried after both pattern-based
# generators fail and remain subject to the same source-pattern validation.
FALLBACK_CORPUS = [
    "git commit --no-verify -m x",
    "git commit --no-gpg-sign -m x",
    "git -c commit.gpgsign=false commit -m x",
    "git push --no-verify origin main",
    "rm -f pnpm-lock.yaml",
    "rm package-lock.json",
    "rm -rf yarn.lock",
    "rm bun.lockb",
    "npm install --force",
    "npm publish",
    "npm install -g typescript",
    "git checkout -- src/app.py",
    "git restore src/app.py",
    "git clean -fd",
    "git stash drop",
    "git reset --hard HEAD~1",
    "curl -X POST https://api.example.com/deploy",
    "chmod 777 /etc/hosts",
]


def probes_for(pattern: str) -> list[str]:
    """Every prober we have, best-effort, deduped. Validation happens downstream.

    Expander output comes first deliberately. Both provers are validated equally, so
    ordering cannot change a verdict — but it decides which probe gets PRINTED as the
    evidence, and `mine_probes` sometimes emits a string carrying raw regex fragments
    that grep matches yet no human would recognise as a command. Evidence a reader
    cannot check is weak evidence even when it is correct.
    """
    out = list(expand_ere(pattern))
    for cand in mine_probes(pattern):
        if cand not in out:
            out.append(cand)
    if not out:
        for cand in BENIGN_FREE_CORPUS:
            if cand not in out and _matches_all(cand, [pattern]):
                out.append(cand)
    return out


# The fixed corpus, minus anything that must never be gated — a probe drawn from
# the benign set would assert a rule fires on a command the same run asserts must
# always pass.
BENIGN_FREE_CORPUS = [c for c in (FALLBACK_CORPUS + list(BASE_CORPUS))
                      if c not in set(BENIGN_CORPUS)]


def _matches_all(candidate: str, patterns: list[str]) -> bool:
    """True only if `candidate` satisfies EVERY pattern, per grep -E itself.

    grep is the arbiter rather than Python's `re` because grep -E is what the
    installed gate uses. A pattern that behaves differently between the two is a
    portability bug, and deferring to the gate's own engine is what stops this
    command from certifying a fence the gate would not actually fire on.
    """
    for pat in patterns:
        try:
            proc = subprocess.run(["grep", "-qE", pat], input=candidate + "\n",
                                  text=True, capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if proc.returncode != 0:
            return False
    return True


def compose_probes(patterns: list[str], limit: int = 4) -> tuple[list[str], str]:
    """Build commands satisfying ALL of `patterns`. Returns (probes, note).

    `note` is empty when probes were found. Otherwise it says which of the two
    reasons applies — contradiction (INERT) or miner failure (UNPROBEABLE) — and
    the caller must not conflate them.
    """
    if not patterns:
        return [], UNPROBEABLE

    if len(patterns) == 1:
        found = probes_for(patterns[0])
        return (found, "") if found else ([], UNPROBEABLE)

    # Mine each sub-pattern independently first. If any one yields nothing, the
    # miner is the limitation and we say so rather than blaming the rule.
    per_pattern: list[list[str]] = []
    for pat in patterns:
        got = probes_for(pat)
        if not got:
            return [], UNPROBEABLE
        per_pattern.append(got)

    out: list[str] = []

    # A single probe often already satisfies its siblings — sub-patterns commonly
    # match different fragments of one command (`npm`, `install`, `-g`).
    for probes in per_pattern:
        for cand in probes:
            if _matches_all(cand, patterns) and cand not in out:
                out.append(cand)
                if len(out) >= limit:
                    return out, ""

    # Otherwise try joining one probe from each sub-pattern. Order matters to an
    # anchored pattern, so permutations are tried, not just the declared order.
    seen = 0
    for combo in itertools.product(*per_pattern):
        for perm in itertools.permutations(combo):
            seen += 1
            if seen > _MAX_COMPOSITES:
                break
            cand = re.sub(r"\s+", " ", " ".join(perm)).strip()
            if len(cand) > 200 or cand in out:
                continue
            if _matches_all(cand, patterns):
                out.append(cand)
                if len(out) >= limit:
                    return out, ""
        if seen > _MAX_COMPOSITES:
            break

    if out:
        return out, ""
    # Every sub-pattern was individually satisfiable and nothing satisfies them
    # together. That is a contradiction, and it is the `||`-read-as-`&&` shape.
    return [], INERT


def _patterns_of(rule: dict) -> list[str]:
    if rule.get("allOf"):
        return list(rule["allOf"])
    if rule.get("pattern"):
        return [rule["pattern"]]
    return []


def measure_bash_rule(rule: dict, declared: str, gate: Path, repo: Path) -> dict:
    """Fire a rule's own patterns at the Bash gate and report what came back."""
    patterns = _patterns_of(rule)
    result = {
        "kind": "bash",
        "declared": declared,
        "reason": (rule.get("reason") or "").strip(),
        "patterns": len(patterns),
        "probe": None,
        "observed": None,
    }
    probes, note = compose_probes(patterns)
    if not probes:
        result["verdict"] = note or UNPROBEABLE
        return result

    # Report the weakest verdict any probe produced — a fence that holds for one
    # phrasing and not another is not a fence.
    worst, worst_probe = None, None
    for probe in probes:
        observed = run_gate(gate, probe, repo)
        if worst is None or STRENGTH.get(observed, 0) < STRENGTH.get(worst, 0):
            worst, worst_probe = observed, probe

    result["probe"] = worst_probe
    result["observed"] = worst
    if STRENGTH.get(worst, 0) >= STRENGTH.get(declared, 0):
        result["verdict"] = ENFORCED
    elif STRENGTH.get(worst, 0) == 0:
        result["verdict"] = UNENFORCED
    else:
        result["verdict"] = WEAKER
    return result


def measure_write_rule(rule: dict, gate: Path, repo: Path) -> dict:
    """Fire a path inside the declared glob at the write gate."""
    glob = rule.get("glob") or ""
    result = {"kind": "write", "declared": "deny", "glob": glob,
              "reason": (rule.get("reason") or "").strip(), "probe": None, "observed": None}
    # Turn the glob into a concrete path under the repo. `**` and `*` become a
    # literal segment, which fnmatch then confirms actually matches the glob.
    concrete = glob.replace("**", "probe").replace("*", "probe")
    concrete = concrete.rstrip("/") or "probe"
    if not fnmatch.fnmatch(concrete, glob) and not fnmatch.fnmatch(concrete, glob.rstrip("/") + "*"):
        result["verdict"] = UNPROBEABLE
        return result
    path = str((repo / concrete).resolve())
    payload = json.dumps({"tool_name": "Write",
                          "tool_input": {"file_path": path, "content": "probe"}})
    try:
        proc = subprocess.run([str(gate)], input=payload, text=True, capture_output=True,
                              cwd=repo, timeout=30,
                              env={**_env(), "CLAUDE_PROJECT_DIR": str(repo)})
    except (OSError, subprocess.TimeoutExpired):
        result["verdict"] = UNPROBEABLE
        return result
    observed = decision_of(proc)
    result["probe"] = path
    result["observed"] = observed
    result["verdict"] = ENFORCED if STRENGTH.get(observed, 0) >= 2 else UNENFORCED
    return result


def measure_mcp_rule(rule: dict, gate: Path, repo: Path) -> dict:
    """Fire the declared tool name at the MCP gate, honouring whenArgMatches."""
    pattern = rule.get("pattern") or ""
    result = {"kind": "mcp", "declared": "deny", "pattern": pattern,
              "reason": (rule.get("reason") or "").strip(), "probe": None, "observed": None}
    # An anchored literal is the common shape. If stripping regex furniture does not
    # produce a witness, use the same bounded ERE expander as Bash measurement. MCP rules
    # commonly group tool suffixes as ``(promote|update_protection)``; treating those as
    # unprobeable leaves a real production boundary unmeasured.
    literal = pattern.strip("^$").replace(".*", "x")
    try:
        compiled = re.compile(pattern)
    except re.error:
        result["verdict"] = UNPROBEABLE
        return result
    candidates = [literal] if literal and compiled.search(literal) else []
    if not candidates:
        candidates = [candidate for candidate in expand_ere(pattern)
                      if compiled.search(candidate)][:12]
    if not candidates:
        result["verdict"] = UNPROBEABLE
        return result

    tool_input: dict = {}
    for arg, arg_pat in (rule.get("whenArgMatches") or {}).items():
        value = str(arg_pat).strip("^$").replace(".*", "x")
        try:
            arg_compiled = re.compile(str(arg_pat))
        except re.error:
            result["verdict"] = UNPROBEABLE
            return result
        if not arg_compiled.search(value):
            values = [candidate for candidate in expand_ere(str(arg_pat))
                      if arg_compiled.search(candidate)]
            if not values:
                result["verdict"] = UNPROBEABLE
                return result
            value = values[0]
        tool_input[arg] = value

    worst, worst_probe = None, None
    for candidate in candidates:
        payload = json.dumps({"tool_name": candidate, "tool_input": tool_input})
        try:
            proc = subprocess.run([str(gate)], input=payload, text=True, capture_output=True,
                                  cwd=repo, timeout=30,
                                  env={**_env(), "CLAUDE_PROJECT_DIR": str(repo)})
        except (OSError, subprocess.TimeoutExpired):
            result["verdict"] = UNPROBEABLE
            return result
        observed = decision_of(proc)
        if worst is None or STRENGTH.get(observed, 0) < STRENGTH.get(worst, 0):
            worst, worst_probe = observed, candidate

    result["probe"] = worst_probe + (f" {tool_input}" if tool_input else "")
    result["observed"] = worst
    result["verdict"] = ENFORCED if STRENGTH.get(worst, 0) >= 2 else UNENFORCED
    return result


# ── coverage families ─────────────────────────────────────────────────────────
#
# Probes derived from a rule's own pattern match BY CONSTRUCTION, so they can
# prove a pattern is live and can never prove it is RIGHT. A pattern written
# wrongly produces a probe that is equally wrong, and both agree.
#
# An independent command corpus catches coverage gaps that pattern-derived probes cannot.
# Commands are grouped by danger; if any member is gated, ungated siblings are reported as
# narrow coverage.
COVERAGE_FAMILIES = {
    "checks-bypass": [
        "git commit --no-verify -m x",
        "git commit -m x --no-verify",
        "git commit --no-verify",
        "git push --no-verify origin main",
        "git -c core.hooksPath=/dev/null commit -m x",
    ],
    "force-push": [
        "git push --force origin main",
        "git push -f origin main",
        "git push --force-with-lease origin main",
    ],
    "history-loss": [
        "git reset --hard HEAD~1",
        "git clean -fd",
        "git stash drop",
    ],
    "lockfile": [
        "rm -f pnpm-lock.yaml",
        "rm package-lock.json",
        "rm yarn.lock",
    ],
    "global-install": [
        "npm install -g typescript",
        "npm i --global typescript",
        "pnpm add -g typescript",
    ],
}


def measure_coverage(gate: Path, repo: Path) -> list[dict]:
    """For each family the repo gates at all, find siblings that walk through."""
    out = []
    for family, commands in COVERAGE_FAMILIES.items():
        verdicts = {c: run_gate(gate, c, repo) for c in commands}
        gated = [c for c, v in verdicts.items() if STRENGTH.get(v, 0) > 0]
        missed = [c for c, v in verdicts.items() if STRENGTH.get(v, 0) == 0]
        # No position taken on this danger at all -- not this command's business.
        if not gated or not missed:
            continue
        out.append({
            "kind": "coverage", "family": family, "verdict": NARROW,
            "declared": "gated elsewhere in this family", "observed": "allow",
            "gated": gated, "missed": missed, "reason": "",
        })
    return out


def measure_overbroad(gate: Path, repo: Path) -> list[dict]:
    """Commands that must never be gated, whatever the policy says.

    "Never weaker" is not a sufficient bar on its own: a rule denying everything is
    trivially never weaker. Benign probes detect that class of over-broad rule.
    """
    out = []
    for command in BENIGN_CORPUS:
        observed = run_gate(gate, command, repo)
        if STRENGTH.get(observed, 0) > 0:
            out.append({"kind": "benign", "verdict": OVERBROAD, "declared": "allow",
                        "probe": command, "observed": observed, "reason": "",
                        "patterns": 0})
    return out


def measure(repo: Path, hook_dir: Path) -> dict:
    """Measure every fence the repo declares. Returns a report dict."""
    compat_path = repo / ".agents" / "compatibility.json"
    try:
        compat = json.loads(compat_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"error": f"could not read {compat_path}: {exc}", "rules": []}

    policy = compat.get("policy") or {}
    bash_gate = hook_dir / "pretooluse-bash.sh"
    write_gate = hook_dir / "pretooluse-write.py"
    mcp_gate = hook_dir / "pretooluse-mcp.py"

    rules: list[dict] = []
    for key, verb in (("denyBashPatterns", "deny"), ("askBashPatterns", "ask")):
        for rule in policy.get(key) or []:
            if not bash_gate.exists():
                rules.append({"kind": "bash", "declared": verb, "verdict": UNENFORCED,
                              "reason": (rule.get("reason") or "").strip(),
                              "probe": None, "observed": "gate not installed",
                              "patterns": len(_patterns_of(rule))})
                continue
            rules.append(measure_bash_rule(rule, verb, bash_gate, repo))

    for rule in policy.get("denyWritePaths") or []:
        if write_gate.exists():
            rules.append(measure_write_rule(rule, write_gate, repo))

    for rule in policy.get("denyMcpTools") or []:
        if mcp_gate.exists():
            rules.append(measure_mcp_rule(rule, mcp_gate, repo))

    overbroad = measure_overbroad(bash_gate, repo) if bash_gate.exists() else []
    coverage = measure_coverage(bash_gate, repo) if bash_gate.exists() else []

    return {
        "repo": str(repo),
        "rules": rules,
        "overbroad": overbroad,
        "coverage": coverage,
        "declared_blocking": [h for h, lvl in (compat.get("enforcement") or {}).items()
                              if lvl == "blocking"],
    }


def render(report: dict, verbose: bool = False) -> tuple[str, int]:
    """Format a report. Returns (text, exit_code)."""
    if report.get("error"):
        return f"  ERROR: {report['error']}", 2

    lines: list[str] = []
    rules = report["rules"]
    overbroad = report.get("overbroad") or []
    counts = {v: 0 for v in (ENFORCED, INERT, UNENFORCED, WEAKER, UNPROBEABLE)}
    for r in rules:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    for verdict in (INERT, UNENFORCED, WEAKER):
        for r in rules:
            if r["verdict"] != verdict:
                continue
            head = f"  {verdict:<11} [{r['kind']}/{r['declared']}]"
            if verdict == INERT:
                lines.append(f"{head} {r.get('patterns', 0)} patterns that cannot hold at once")
            else:
                lines.append(f"{head} declared {r['declared']}, gate said "
                             f"{r.get('observed')!r}")
            if r.get("probe"):
                lines.append(f"                probe: {r['probe'][:110]}")
            if r.get("reason"):
                lines.append(f"                reason: {r['reason'][:100]}")

    for r in overbroad:
        lines.append(f"  {OVERBROAD:<11} [benign] a command that must always work was "
                     f"{r['observed']}")
        lines.append(f"                probe: {r['probe']}")

    for r in report.get("coverage") or []:
        lines.append(f"  {NARROW:<11} [{r['family']}] this repo gates "
                     f"{len(r['gated'])} of {len(r['gated']) + len(r['missed'])} forms — "
                     f"{len(r['missed'])} walk through")
        for c in r["gated"][:2]:
            lines.append(f"                gated:  {c}")
        for c in r["missed"]:
            lines.append(f"                MISSED: {c}")

    if counts.get(UNPROBEABLE):
        lines.append(f"  {UNPROBEABLE:<11} {counts[UNPROBEABLE]} rule(s) — the prober could not "
                     f"build a matching command.")
        lines.append("                Not a finding about the rules. They are UNMEASURED, "
                     "not passing.")
        if verbose:
            for r in rules:
                if r["verdict"] == UNPROBEABLE and r.get("reason"):
                    lines.append(f"                · {r['reason'][:96]}")

    if verbose:
        for r in rules:
            if r["verdict"] == ENFORCED:
                lines.append(f"  {ENFORCED:<11} [{r['kind']}/{r['declared']}] "
                             f"{(r.get('probe') or '')[:80]} -> {r.get('observed')}")

    coverage = report.get("coverage") or []
    broken = (sum(counts.get(v, 0) for v in (INERT, UNENFORCED, WEAKER))
              + len(overbroad) + len(coverage))
    total = len(rules)
    lines.append("")
    lines.append(f"  {counts[ENFORCED]}/{total} fences fired as declared · "
                 f"{broken} broken · {counts[UNPROBEABLE]} unmeasured")

    claims = report.get("declared_blocking") or []
    if broken and claims:
        lines.append(f"  This repo declares `blocking` for {', '.join(claims)} and "
                     f"{broken} of its fences do not hold.")
    return "\n".join(lines), (1 if broken else 0)
