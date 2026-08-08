#!/usr/bin/env python3
"""agentkit test suite. No dependencies — `python3 kit/tests/run_tests.py`.

Deliberately dependency-free: this has to run in 41 repositories, in CI, and on a fresh
clone with nothing installed. A test suite that needs its own install is a test suite
people skip.

The tests that matter most are the NEGATIVE ones. A safety gate that allows everything
passes any test that only checks the happy path, and that is precisely the failure this
kit was built to fix: the ancestor gate exited 0 when it could not parse its input, and
nothing noticed for months. So: every gate is tested for what it must REFUSE, and the
fail-closed path is tested directly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
HOOKS = KIT / "hooks" / "claude"
RESIDUE = KIT / "verify" / "residue.py"

# `agentkit` has no .py suffix, so it needs an explicit loader to import.
sys.path.insert(0, str(KIT))
import importlib.util as _ilu
from importlib.machinery import SourceFileLoader as _SFL
_spec = _ilu.spec_from_loader("agentkit_mod", _SFL("agentkit_mod", str(KIT / "agentkit")))
agentkit_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(agentkit_mod)

PASSED, FAILED = 0, 0
CURRENT = ""


def section(name: str) -> None:
    global CURRENT
    CURRENT = name
    print(f"\n{name}")


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  pass  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


def run_hook(name: str, payload, repo: Path) -> tuple[int, str, str]:
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(repo))
    body = payload if isinstance(payload, str) else json.dumps(payload)
    proc = subprocess.run([str(HOOKS / name)], input=body, capture_output=True,
                          text=True, env=env, cwd=repo, timeout=30)
    return proc.returncode, proc.stdout, proc.stderr


def decision(code: int, out: str) -> str:
    if code == 2:
        return "blocked-exit2"
    try:
        return json.loads(out)["hookSpecificOutput"]["permissionDecision"]
    except Exception:
        return "allow"


def context_of(out: str) -> str:
    try:
        return json.loads(out)["hookSpecificOutput"].get("additionalContext", "")
    except Exception:
        return ""


def git_init(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def git_commit_all(repo: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)


def make_repo(tmp: Path, policy: dict | None = None, compat_extra: dict | None = None) -> Path:
    repo = tmp / "repo"
    (repo / ".agents").mkdir(parents=True, exist_ok=True)
    (repo / ".claude").mkdir(parents=True, exist_ok=True)
    compat = {
        "agentkitVersion": "0.1.0",
        "canonical": {"instructions": "AGENTS.md", "shim": "CLAUDE.md",
                      "project": "docs/project", "skills": ".agents/skills"},
        "enforcement": {"claude-code": "advisory"},
        "autoMemory": "on",
        "budgets": {"codexProjectDocMaxBytes": 32768, "claudeLineTarget": 200},
        "policy": policy or {},
    }
    compat.update(compat_extra or {})
    (repo / ".agents" / "compatibility.json").write_text(json.dumps(compat), encoding="utf-8")
    (repo / "AGENTS.md").write_text("# t\n\n## Never\n- x\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    (repo / "docs" / "project").mkdir(parents=True, exist_ok=True)
    (repo / ".agents" / "skills").mkdir(parents=True, exist_ok=True)
    return repo


# ── the Bash gate ─────────────────────────────────────────────────────────────────────

def test_bash_gate(tmp: Path) -> None:
    section("Bash gate — what it must refuse")
    repo = make_repo(tmp)

    cases = [
        ("force-push to main",          "git push --force origin main",              "deny"),
        ("force-push short flag",       "git push -f origin main",                   "deny"),
        ("--no-verify commit",          "git commit --no-verify -m 'x'",             "deny"),
        ("core.hooksPath bypass",       "git -c core.hooksPath=/dev/null push",      "deny"),
        ("plain push to main",          "git push origin main",                      "ask"),
        ("reset --hard",                "git reset --hard HEAD~1",                   "ask"),
        ("git clean -fd",               "git clean -fd",                             "ask"),
        ("git clean -d -f separated",   "git clean -d -f",                           "ask"),
        ("git restore",                 "git restore src/app.ts",                    "ask"),
        ("git stash drop",              "git stash drop",                            "ask"),
        ("checkout a file",             "git checkout -- src/app.ts",                "ask"),
        ("recursive rm outside safe",   "rm -rf /Users/someone/important",           "ask"),

        # Databases. Measured 2026-08-09: before this rule the gate ALLOWED every
        # one of these. Wiping is blocked; resetting asks, because resetting a local
        # dev database is an everyday command and a gate that blocks routine work is
        # a gate somebody switches off.
        ("DROP TABLE via psql",         "psql -c 'DROP TABLE users'",                "deny"),
        ("drop database, lowercase",    'psql -c "drop database prod"',              "deny"),
        ("redis FLUSHALL",              "redis-cli FLUSHALL",                        "deny"),
        ("mongo dropDatabase",          "mongosh --eval 'db.dropDatabase()'",        "deny"),
        ("TRUNCATE via mysql",          "mysql -e 'TRUNCATE TABLE orders'",          "deny"),
        ("prisma --accept-data-loss",   "npx prisma db push --accept-data-loss",     "deny"),
        ("dropdb takes no verb",        "dropdb production",                         "deny"),
        ("sanity dataset delete",       "npx sanity dataset delete production",      "deny"),
        ("runner prefix: pnpm dlx",     "pnpm dlx prisma db push --accept-data-loss", "deny"),
        ("supabase db reset",           "supabase db reset",                         "ask"),
        ("prisma migrate reset",        "npx prisma migrate reset",                  "ask"),
        ("rails db:reset",              "rails db:reset",                            "ask"),
        ("artisan migrate:fresh",       "php artisan migrate:fresh",                 "ask"),
        ("bundle exec rails db:drop",   "bundle exec rails db:drop",                 "ask"),
        ("wp db drop",                  "wp db drop",                                "deny"),
        ("wp db query DROP TABLE",      "wp db query 'DROP TABLE wp_posts'",         "deny"),
        ("wp db clean",                 "wp db clean --yes",                         "deny"),
        ("wp db reset",                 "wp db reset",                               "ask"),
    ]
    for label, command, expected in cases:
        code, out, _ = run_hook("pretooluse-bash.sh", {"tool_name": "Bash", "tool_input": {"command": command}}, repo)
        got = decision(code, out)
        check(f"{label} -> {expected}", got == expected, f"got {got}")

    section("Bash gate — what it must NOT refuse")
    for label, command in [
        ("plain ls",                 "ls -la"),
        ("checkout -b is safe",      "git checkout -b feature/x"),
        ("push to a feature branch", "git push origin feature/x"),
        ("rm -rf in /tmp",           "rm -rf /tmp/scratch"),
        ("rm -rf a build dir",       "rm -rf ./dist/"),
        ("--no-verify inside a message body", "git commit -m 'note\nmentions --no-verify here'"),

        # The database rule must not eat the ability to AUDIT for the danger. A rule
        # that fires on the words alone blocks the search for them — the same trap
        # that once made a harvested rule deny `ls main.py`.
        ("grepping FOR 'DROP TABLE'",  "grep -rn 'DROP TABLE' ."),
        ("reading a migration file",   "cat migrations/001_drop_table.sql"),
        ("a plain SELECT",             "psql -c 'SELECT * FROM users'"),
        ("prisma generate",            "npx prisma generate"),
        ("a forward migration",        "npx prisma migrate dev"),
        ("supabase status",            "supabase status"),
        ("a seed script",              "npm run db:seed"),
        # `wp` is a whole CMS CLI, not a database tool. Gating the tool rather than
        # the operation would break every routine WordPress command.
        ("wp plugin list",             "wp plugin list"),
        ("wp db export is a BACKUP",   "wp db export backup.sql"),
        ("wp option get",              "wp option get siteurl"),
    ]:
        code, out, _ = run_hook("pretooluse-bash.sh", {"tool_name": "Bash", "tool_input": {"command": command}}, repo)
        got = decision(code, out)
        check(f"{label} -> allow", got in ("allow",), f"got {got}")

    section("Bash gate — a repo cannot lower the universal floor")
    # Measured on a real repo 2026-08-09: its broad WP-CLI ask-rule matched
    # `wp db drop` before the universal database block ran, and because the gate
    # exits on first match, repo policy silently downgraded a hard block to a
    # prompt. A floor a repo can lower is not a floor. So a repo `ask` is HELD
    # until every universal block has had its turn.
    shadow = make_repo(tmp / "shadow", policy={"askBashPatterns": [
        {"pattern": "(^|[^[:alnum:]_])wp([^[:alnum:]_]|$)",
         "reason": "a deliberately broad rule that matches everything wp"}]})
    for label, command, expected in [
        ("repo ask cannot soften a DB block",  "wp db drop",                    "deny"),
        ("nor a checks-bypass block",          "wp x && git commit --no-verify -m x", "deny"),
        ("but the repo ask still fires alone", "wp plugin list",                "ask"),
    ]:
        code, out, _ = run_hook("pretooluse-bash.sh",
                                {"tool_name": "Bash", "tool_input": {"command": command}}, shadow)
        got = decision(code, out)
        check(f"{label} -> {expected}", got == expected, f"got {got}")

    section("Bash gate — fails CLOSED (the bug this kit exists to fix)")
    code, _, err = run_hook("pretooluse-bash.sh", "this is not json", repo)
    check("malformed payload exits 2", code == 2, f"exit {code}")
    check("and explains itself on stderr", "agentkit safety gate" in err, err[:80])

    section("Bash gate — cwd context injection")
    code, out, _ = run_hook("pretooluse-bash.sh", {"tool_name": "Bash", "tool_input": {"command": "git add ."}}, repo)
    check("mutative op injects cwd context", "[cwd-safety]" in context_of(out), out[:120])


# ── the write gate ────────────────────────────────────────────────────────────────────

def test_write_gate(tmp: Path) -> None:
    section("Edit/Write gate — path denials")
    policy = {
        "denyWritePaths": [
            {"glob": "lib/content/live/**", "reason": "generated from the CMS; hand-edits are lost on the next sync."},
        ],
        "measureOnWrite": [
            {"glob": "lib/content/**", "measure": "script-lengths", "scripts": ["Cyrillic"],
             "reason": "the operator cannot read Cyrillic and needs a number."},
        ],
    }
    repo = make_repo(tmp, policy)
    (repo / "lib" / "content" / "live").mkdir(parents=True, exist_ok=True)

    denied = str(repo / "lib" / "content" / "live" / "page.json")
    code, out, _ = run_hook("pretooluse-write.py", {"tool_name": "Write", "tool_input": {"file_path": denied}}, repo)
    check("denied path is refused", decision(code, out) == "deny", out[:160])
    check("denial names the reason", "hand-edits are lost" in out, out[:160])

    allowed = str(repo / "src" / "app.ts")
    code, out, _ = run_hook("pretooluse-write.py", {"tool_name": "Write", "tool_input": {"file_path": allowed}}, repo)
    check("unrelated path allowed", decision(code, out) == "allow", out[:160])

    section("Edit/Write gate — the measurement that must never be deleted")
    target = repo / "lib" / "content" / "loc.json"
    target.write_text(json.dumps({"title": "Привет мир"}, ensure_ascii=False), encoding="utf-8")
    code, out, _ = run_hook("pretooluse-write.py", {"tool_name": "Edit", "tool_input": {"file_path": str(target)}}, repo)
    ctx = context_of(out)
    check("before-measurement is taken", "BEFORE" in ctx and "Cyrillic" in ctx, ctx[:160])

    target.write_text(json.dumps({"title": "Привет"}, ensure_ascii=False), encoding="utf-8")
    code, out, _ = run_hook("posttooluse-write.py", {"tool_name": "Edit", "tool_input": {"file_path": str(target)}}, repo)
    ctx = context_of(out)
    check("after-measurement reports a delta", "AFTER" in ctx and "Cyrillic" in ctx, ctx[:200])
    check("a shrinking Cyrillic count is flagged CHANGED", "CHANGED" in ctx, ctx[:200])

    section("Edit/Write gate — corrupt policy fails CLOSED")
    broken = make_repo(tmp / "broken")
    (broken / ".agents" / "compatibility.json").write_text("{ not json", encoding="utf-8")
    code, _, err = run_hook("pretooluse-write.py", {"tool_name": "Write", "tool_input": {"file_path": "/x/y.ts"}}, broken)
    check("unreadable policy exits 2", code == 2, f"exit {code}")
    check("and says why", "could not be read" in err, err[:100])


# ── the MCP gate ──────────────────────────────────────────────────────────────────────

def test_mcp_gate(tmp: Path) -> None:
    section("MCP gate — the surface a Bash-only matcher cannot see")
    policy = {"denyMcpTools": [
        {"pattern": "^mcp__.*__deploy_to_vercel$",
         "reason": "cutover is an operator-run event with a rollback plan.",
         "whenArgMatches": {"target": "^production$"}},
        {"pattern": "^mcp__.*__delete_database$", "reason": "no."},
    ]}
    repo = make_repo(tmp / "mcp", policy)

    code, out, _ = run_hook("pretooluse-mcp.py", {
        "tool_name": "mcp__plugin_vercel_vercel__deploy_to_vercel",
        "tool_input": {"target": "production"}}, repo)
    check("production deploy denied", decision(code, out) == "deny", out[:160])

    code, out, _ = run_hook("pretooluse-mcp.py", {
        "tool_name": "mcp__plugin_vercel_vercel__deploy_to_vercel",
        "tool_input": {"target": "preview"}}, repo)
    check("preview deploy allowed (narrowing works)", decision(code, out) == "allow", out[:160])

    code, out, _ = run_hook("pretooluse-mcp.py", {
        "tool_name": "mcp__anything__delete_database", "tool_input": {}}, repo)
    check("unconditional rule denies with no args", decision(code, out) == "deny", out[:160])

    code, out, _ = run_hook("pretooluse-mcp.py", {
        "tool_name": "mcp__other__read_thing", "tool_input": {}}, repo)
    check("unrelated MCP tool allowed", decision(code, out) == "allow", out[:160])

    section("MCP gate — an invalid regex must refuse, not skip")
    bad = make_repo(tmp / "badre", {"denyMcpTools": [{"pattern": "^mcp__(unclosed", "reason": "x"}]})
    code, _, err = run_hook("pretooluse-mcp.py", {"tool_name": "mcp__a__b", "tool_input": {}}, bad)
    check("bad regex exits 2", code == 2, f"exit {code}")
    check("and names the pattern", "invalid regex" in err, err[:120])


# ── the ConfigChange guard ────────────────────────────────────────────────────────────

def test_configchange(tmp: Path) -> None:
    section("ConfigChange guard — disarming the gates is loud")
    repo = make_repo(tmp / "cfg")
    (repo / ".claude" / "settings.json").write_text(json.dumps({
        "permissions": {"deny": ["Write(lib/content/live/**)"]},
        "hooks": {"PreToolUse": [], "PostToolUse": []},
    }), encoding="utf-8")

    code, _, err = run_hook("configchange-guard.py", {
        "file_path": str(repo / ".claude" / "settings.json"),
        "new_settings": {"disableAllHooks": True}}, repo)
    check("disableAllHooks refused", code == 2, f"exit {code}")
    check("and explains the blast radius", "every safety gate" in err, err[:120])

    code, _, err = run_hook("configchange-guard.py", {
        "file_path": str(repo / ".claude" / "settings.json"),
        "new_settings": {"permissions": {"deny": []}, "hooks": {"PreToolUse": [], "PostToolUse": []}}}, repo)
    check("emptying permissions.deny refused", code == 2, f"exit {code}")

    code, _, err = run_hook("configchange-guard.py", {
        "file_path": str(repo / ".claude" / "settings.json"),
        "new_settings": {"permissions": {"deny": ["Write(lib/content/live/**)"]},
                         "hooks": {"PreToolUse": []}}}, repo)
    check("dropping a hook event refused", code == 2, f"exit {code}")

    code, _, _ = run_hook("configchange-guard.py", {
        "file_path": str(repo / ".claude" / "settings.json"),
        "new_settings": {"permissions": {"deny": ["Write(lib/content/live/**)"], "allow": ["Bash(ls:*)"]},
                         "hooks": {"PreToolUse": [], "PostToolUse": []}}}, repo)
    check("an innocuous settings change is allowed", code == 0, f"exit {code}")


# ── the residue verifier ──────────────────────────────────────────────────────────────

def residue(repo: Path) -> dict:
    proc = subprocess.run([sys.executable, str(RESIDUE), "--repo", str(repo), "--json"],
                          capture_output=True, text=True, timeout=60)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"errors": [f"<residue crashed: {proc.stderr[:200]}>"], "warnings": [], "notes": []}


def has(report: dict, key: str, needle: str) -> bool:
    return any(needle in item for item in report[key])


def test_residue(tmp: Path) -> None:
    section("Residue verifier — amputation vectors")

    repo = make_repo(tmp / "r1")
    (repo / ".codex").mkdir(exist_ok=True)
    (repo / ".codex" / "config.toml").write_text("project_doc_max_bytes = 100\n", encoding="utf-8")
    rep = residue(repo)
    check("repo .codex/config.toml cap is an error", has(rep, "errors", "project_doc_max_bytes"), str(rep["errors"])[:160])

    repo = make_repo(tmp / "r2")
    (repo / ".codex").mkdir(exist_ok=True)
    (repo / ".codex" / "config.toml").write_text('project_doc_fallback_filenames = ["X.md"]\n', encoding="utf-8")
    rep = residue(repo)
    check("fallback filenames is an error", has(rep, "errors", "fallback"), str(rep["errors"])[:160])

    repo = make_repo(tmp / "r3")
    (repo / ".claude" / "settings.json").write_text(json.dumps({"claudeMdExcludes": ["**/CLAUDE.md"]}), encoding="utf-8")
    rep = residue(repo)
    check("claudeMdExcludes is an error", has(rep, "errors", "claudeMdExcludes"), str(rep["errors"])[:160])

    repo = make_repo(tmp / "r4")
    (repo / "AGENTS.override.md").write_text("x\n", encoding="utf-8")
    rep = residue(repo)
    check("AGENTS.override.md is an error", has(rep, "errors", "override"), str(rep["errors"])[:160])

    repo = make_repo(tmp / "r5")
    (repo / "sub").mkdir(exist_ok=True)
    (repo / "sub" / "AGENTS.md").write_text("x\n", encoding="utf-8")
    rep = residue(repo)
    check("nested AGENTS.md is reported", has(rep, "errors", "nested") or has(rep, "todos", "nested"),
          str(rep)[:200])

    section("Residue verifier — the checks nothing else performs")

    repo = make_repo(tmp / "r6")
    (repo / ".claude" / "settings.json").write_text(json.dumps({
        "permissions": {"allow": ["Bash(${CLAUDE_PROJECT_DIR}/.claude/hooks/gone.sh:*)"]}}), encoding="utf-8")
    rep = residue(repo)
    check("dead permissions.allow reference is an error", has(rep, "errors", "gone.sh"), str(rep["errors"])[:200])

    repo = make_repo(tmp / "r6b")
    (repo / ".claude" / "scripts").mkdir(parents=True, exist_ok=True)
    (repo / ".claude" / "scripts" / "live.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PostToolUse": [
        {"matcher": "Write|Edit", "hooks": [{"type": "command",
         "command": 'bash "$CLAUDE_PROJECT_DIR/.claude/scripts/live.sh" "$CLAUDE_FILE" || true'}]},
        {"matcher": "Write", "hooks": [{"type": "command",
         "command": 'bash "$CLAUDE_PROJECT_DIR/.claude/scripts/gone.sh"'}]}]}}), encoding="utf-8")
    (repo / ".claude" / "scripts" / "live.sh").chmod(0o755)
    rep = residue(repo)
    check("an interpreter-prefixed LIVE script is not reported",
          not has(rep, "errors", "live.sh"), str(rep["errors"])[:200])
    check("an interpreter-prefixed DEAD script IS reported",
          has(rep, "errors", "gone.sh"), str(rep["errors"])[:200])

    repo = make_repo(tmp / "r7")
    (repo / ".claude" / "settings.json").write_text(json.dumps({"autoMemoryEnabled": False}), encoding="utf-8")
    rep = residue(repo)
    check("autoMemory declaration mismatch is an error", has(rep, "errors", "autoMemory"), str(rep["errors"])[:200])

    repo = make_repo(tmp / "r8")
    rules = repo / ".claude" / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    (rules / "unscoped.md").write_text("# always loaded\n", encoding="utf-8")
    rep = residue(repo)
    check("unscoped .claude/rules is reported",
          has(rep, "errors", "paths") or has(rep, "todos", "paths"), str(rep)[:200])

    (rules / "unscoped.md").write_text('---\npaths:\n  - "src/**/*.ts"\n---\n# scoped\n', encoding="utf-8")
    rep = residue(repo)
    check("path-scoped .claude/rules is accepted",
          not has(rep, "errors", "paths") and not has(rep, "todos", "paths"), str(rep)[:200])

    section("Residue verifier — stage separates 'not migrated' from 'broken'")
    # Ten un-migrated repos must not look like ten broken ones. Migration findings are TODOs
    # while the repo is mid-migration; a genuinely broken thing is an error at every stage.
    repo = make_repo(tmp / "r13")
    (repo / ".agent-docs").mkdir(exist_ok=True)
    rules = repo / ".claude" / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    (rules / "big.md").write_text("# unscoped\n", encoding="utf-8")
    rep = residue(repo)
    check("mid-migration repo reports stage 'mechanical'", rep.get("stage") == "mechanical", str(rep.get("stage")))
    check("unscoped rules are a TODO, not an error", has(rep, "todos", "paths"), str(rep)[:200])
    check("an empty policy is flagged as a WARNING", has(rep, "warnings", "policy is EMPTY"), str(rep["warnings"])[:200])

    # Same defect, but the repo now claims to be finished: it becomes an error.
    repo = make_repo(tmp / "r14")
    rules = repo / ".claude" / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    (rules / "big.md").write_text("# unscoped\n", encoding="utf-8")
    (repo / "docs" / "project").mkdir(parents=True, exist_ok=True)
    rep = residue(repo)
    check("a repo claiming 'migrated' turns the same finding into an ERROR",
          rep.get("stage") == "migrated" and has(rep, "errors", "paths"), str(rep)[:220])

    section("Residue verifier — skills adapter, including the bug that broke the ancestor")

    repo = make_repo(tmp / "r9")
    skill = repo / ".agents" / "skills" / "demo"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\nbody\n", encoding="utf-8")
    claude_skills = repo / ".claude" / "skills"
    claude_skills.mkdir(parents=True, exist_ok=True)
    (claude_skills / "demo").symlink_to(os.path.relpath(skill, claude_skills))
    rep = residue(repo)
    check("a conforming relative symlink passes", not has(rep, "errors", "demo"), str(rep["errors"])[:200])

    # This is the exact shape that hard-failed the reference repo's validator: Claude Code
    # writes a REAL directory here when /verify runs.
    (claude_skills / "verify").mkdir(exist_ok=True)
    (claude_skills / "verify" / "SKILL.md").write_text("---\nname: verify\ndescription: d\n---\n", encoding="utf-8")
    rep = residue(repo)
    check("harness-written .claude/skills/verify is tolerated",
          not has(rep, "errors", "skills/verify"), str(rep["errors"])[:200])

    (claude_skills / "handmade").mkdir(exist_ok=True)
    rep = residue(repo)
    check("an UNdeclared real directory is still an error",
          has(rep, "errors", "handmade"), str(rep["errors"])[:200])

    absolute = repo / ".claude" / "skills" / "abs"
    absolute.symlink_to(skill)  # absolute target
    rep = residue(repo)
    check("an absolute symlink is an error", has(rep, "errors", "ABSOLUTE"), str(rep["errors"])[:200])

    section("Residue verifier — budgets")

    repo = make_repo(tmp / "r10")
    (repo / "AGENTS.md").write_text("x" * 40000, encoding="utf-8")
    rep = residue(repo)
    check("AGENTS.md over the Codex cap is an error", has(rep, "errors", "over Codex"), str(rep["errors"])[:200])

    repo = make_repo(tmp / "r11")
    (repo / "AGENTS.md").write_text("\n".join(f"- rule {i}" for i in range(400)), encoding="utf-8")
    rep = residue(repo)
    check("over the Claude line target is a WARNING, not an error",
          has(rep, "warnings", "line target") and not has(rep, "errors", "line target"),
          str(rep)[:220])

    repo = make_repo(tmp / "r12")
    (repo / "docs" / "project").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "project" / "pitfalls.md").write_text("\n".join(f"line {i}" for i in range(200)), encoding="utf-8")
    rep = residue(repo)
    check("pitfalls.md over its cap is an error", has(rep, "errors", "pitfalls"), str(rep["errors"])[:200])


# ── apply idempotence ─────────────────────────────────────────────────────────────────

def test_apply(tmp: Path) -> None:
    section("apply — idempotent, and never writes truth")
    repo = tmp / "apply-target"
    repo.mkdir(parents=True, exist_ok=True)
    git_init(repo)
    (repo / "AGENTS.md").write_text("# proj\n\n## Never\n- do not\n", encoding="utf-8")
    original = (repo / "AGENTS.md").read_bytes()

    section("preflight — a dirty tree is refused before anything is written")
    dirty = subprocess.run([sys.executable, str(KIT / "agentkit"), "apply", "--repo", str(repo)],
                           capture_output=True, text=True, timeout=120)
    check("dirty tree refused", dirty.returncode == 2, f"exit {dirty.returncode}")
    check("nothing was written", not (repo / ".agents").exists())
    check("and it says how to proceed", "--allow-dirty" in dirty.stderr, dirty.stderr[:160])

    section("apply --dry-run — preview without touching anything")
    preview = subprocess.run([sys.executable, str(KIT / "agentkit"), "apply", "--repo", str(repo),
                              "--dry-run"], capture_output=True, text=True, timeout=120)
    check("dry-run succeeds on a dirty tree", preview.returncode == 0, preview.stderr[:160])
    check("dry-run announces the footprint", "[dry-run]" in preview.stdout, preview.stdout[:200])
    check("dry-run wrote nothing", not (repo / ".agents").exists())

    section("apply — idempotent, and never writes truth")
    git_commit_all(repo)
    first = subprocess.run([sys.executable, str(KIT / "agentkit"), "apply", "--repo", str(repo)],
                           capture_output=True, text=True, timeout=120)
    check("first apply succeeds", first.returncode == 0, first.stderr[:200])
    check("compatibility.json created", (repo / ".agents" / "compatibility.json").exists())
    check("settings.json created", (repo / ".claude" / "settings.json").exists())
    check("hooks installed", (repo / ".claude" / "hooks" / "agentkit" / "pretooluse-bash.sh").exists())
    check("AGENTS.md untouched by apply", (repo / "AGENTS.md").read_bytes() == original)

    settings = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    matchers = [g.get("matcher") for g in settings["hooks"]["PreToolUse"]]
    check("three PreToolUse matchers, not one", len(matchers) == 3, str(matchers))
    check("Edit|Write matcher present", any("Edit" in (m or "") for m in matchers), str(matchers))
    check("mcp matcher present", any("mcp" in (m or "") for m in matchers), str(matchers))
    check("ConfigChange hook present", "ConfigChange" in settings["hooks"])

    second = subprocess.run([sys.executable, str(KIT / "agentkit"), "apply", "--repo", str(repo)],
                            capture_output=True, text=True, timeout=120)
    check("second apply is idempotent", "0 change(s)" in second.stdout, second.stdout[-200:])

    section("apply — must NOT disarm a repo that already has hooks")
    # Found against a real fieldbook repo carrying 8 hook entries across 5 events. Assigning
    # settings["hooks"] wholesale deleted every one of them while reporting success.
    legacy = tmp / "legacy-hooks"
    legacy.mkdir(parents=True, exist_ok=True)
    git_init(legacy)
    (legacy / ".claude").mkdir(exist_ok=True)
    (legacy / "AGENTS.md").write_text("# t\n", encoding="utf-8")
    (legacy / ".claude" / "settings.json").write_text(json.dumps({
        "statusLine": {"type": "command", "command": "x"},
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/their-gate.sh"}]}],
            "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/router.sh startup"}]}],
            "PreCompact": [{"hooks": [{"type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/handoff.sh"}]}],
        },
    }, indent=2), encoding="utf-8")
    git_commit_all(legacy)

    out = subprocess.run([sys.executable, str(KIT / "agentkit"), "apply", "--repo", str(legacy)],
                         capture_output=True, text=True, timeout=120)
    merged = json.loads((legacy / ".claude" / "settings.json").read_text(encoding="utf-8"))
    all_commands = [h["command"] for groups in merged["hooks"].values()
                    for g in groups for h in g.get("hooks", [])]
    check("apply reports what it preserved", "preserved 3 existing hook entries" in out.stdout, out.stdout[:400])
    check("their Bash gate survives", any("their-gate.sh" in c for c in all_commands), str(all_commands))
    check("their SessionStart router survives", any("router.sh" in c for c in all_commands), str(all_commands))
    check("their PreCompact hook survives", any("handoff.sh" in c for c in all_commands), str(all_commands))
    check("SessionStart event not dropped", "SessionStart" in merged["hooks"], str(list(merged["hooks"])))
    check("agentkit's own gates added alongside", any("agentkit/pretooluse-bash.sh" in c for c in all_commands), str(all_commands))
    check("both Bash hooks coexist",
          len([c for c in all_commands if "pretooluse-bash.sh" in c or "their-gate.sh" in c]) == 2, str(all_commands))
    check("unrelated statusLine preserved", merged.get("statusLine", {}).get("command") == "x")

    again = subprocess.run([sys.executable, str(KIT / "agentkit"), "apply", "--repo", str(legacy)],
                           capture_output=True, text=True, timeout=120)
    check("re-apply over foreign hooks is still idempotent", "0 change(s)" in again.stdout, again.stdout[-200:])

    # A user key must survive re-apply: apply owns hooks and permissions.deny, nothing else.
    settings["permissions"]["allow"].append("Bash(pnpm build:*)")
    settings["customUserKey"] = "kept"
    (repo / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    subprocess.run([sys.executable, str(KIT / "agentkit"), "apply", "--repo", str(repo)],
                   capture_output=True, text=True, timeout=120)
    after = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    check("user allow-list entry preserved", "Bash(pnpm build:*)" in after["permissions"]["allow"])
    check("unrelated user key preserved", after.get("customUserKey") == "kept")

    section("revert — must never destroy a quarantine it could not restore")
    # It did exactly that once: a manifest whose paths no longer resolved undid zero actions
    # and the tool then rmtree'd the only copy of a retired safety gate.
    stale = tmp / "stale-quarantine"
    stale.mkdir(parents=True, exist_ok=True)
    git_init(stale)
    (stale / "AGENTS.md").write_text("# t\n", encoding="utf-8")
    qdir = Path.home() / ".agentkit" / "quarantine" / stale.name / "20200101T000000Z"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "manifest.json").write_text(json.dumps({"stamp": "x", "actions": [
        {"op": "rename", "from": "nowhere/absent.sh", "to": ".claude/hooks/absent.sh"}]}), encoding="utf-8")
    (qdir / "precious.txt").write_text("the only copy", encoding="utf-8")
    r = subprocess.run([sys.executable, str(KIT / "agentkit"), "revert", "--repo", str(stale)],
                       capture_output=True, text=True, timeout=60)
    check("revert exits non-zero when it restored nothing", r.returncode == 1, f"exit {r.returncode}")
    check("and the quarantine SURVIVES", (qdir / "precious.txt").exists())
    check("and it says where the content is", str(qdir) in r.stderr, r.stderr[:200])
    shutil.rmtree(Path.home() / ".agentkit" / "quarantine" / stale.name, ignore_errors=True)

    section("self-test — the thing that entitles a `blocking` claim")
    st = subprocess.run([sys.executable, str(KIT / "agentkit"), "self-test", "--repo", str(repo)],
                        capture_output=True, text=True, timeout=120)
    check("self-test passes on a fresh install", st.returncode == 0, st.stdout[-400:])
    check("self-test proves fail-closed", "fails CLOSED" in st.stdout, st.stdout[-200:])


def test_policy_and_supersede(tmp: Path) -> None:
    sys.path.insert(0, str(KIT / "lib"))
    import scaffold as sc

    section("policy harvest — inert patterns must never ship")
    check("unresolved shell var is rejected",
          any("unresolved shell variable" in p for p in sc.diagnose(r"${CSEP}railway +up")))
    check("GNU escape is flagged", any("GNU-only" in p for p in sc.diagnose(r"git +push\b")))
    check("a clean POSIX pattern passes", sc.diagnose("(^|[[:space:]])railway +up") == [])

    section("policy harvest — GNU escapes translate to POSIX")
    check(r"\s becomes [[:space:]]", sc.portable_boundaries(r"a\s+b") == "a[[:space:]]+b")
    check(r"\S becomes [^[:space:]]", sc.portable_boundaries(r"\S+") == "[^[:space:]]+")
    check("trailing \\b becomes a trailing boundary",
          sc.portable_boundaries(r"up\b") == "up([^[:alnum:]_]|$)")
    check("leading \\b becomes a leading boundary",
          sc.portable_boundaries(r"\bmain") == "(^|[^[:alnum:]_])main")

    section("policy harvest — over-broad rules are refused, not promoted")
    # The measured failure: harvesting a push-to-protected rule's bare branch matcher
    # produced a policy that denied `ls main.py`.
    branch_only = "(^|[^[:alnum:]_])main([^[:alnum:]_]|$)|(^|[^[:alnum:]_])master([^[:alnum:]_]|$)"
    blocked = sc.over_broad([branch_only])
    check("a bare branch matcher is caught as over-broad", "ls main.py" in blocked, str(blocked))
    check("and the real conjunction is NOT",
          sc.over_broad(["(^|[[:space:]])git +push", branch_only]) == [],
          str(sc.over_broad(["(^|[[:space:]])git +push", branch_only])))

    section("the gate honours allOf — a conjunction needs BOTH halves")
    repo = make_repo(tmp / "conj", {
        "denyBashPatterns": [
            {"allOf": ["(^|[[:space:]])git +push", branch_only],
             "reason": "push to a protected branch is an operator action."}
        ]
    })
    for cmd, expected in [
        ("git push origin main", "deny"),
        ("ls main.py", "allow"),          # second half only — must NOT fire
        ("git push origin feature/x", "allow"),  # first half only — must NOT fire
    ]:
        code, out, _ = run_hook("pretooluse-bash.sh",
                                {"tool_name": "Bash", "tool_input": {"command": cmd}}, repo)
        got = decision(code, out)
        check(f"{cmd!r} -> {expected}", got == expected, f"got {got}")

    section("protectedBranches is read from policy, not hardcoded")
    # Found on a repo whose live deploy branch is `beast`: the gate hardcoded (main|master)
    # while the schema documented policy.protectedBranches, so force-pushing the deploy
    # branch was allowed by a repo that believed it was protected.
    repo = make_repo(tmp / "branches", {"protectedBranches": ["main", "beast"]})
    for cmd, expected in [
        ("git push --force origin beast", "deny"),
        ("git push origin beast", "ask"),
        ("git push --force origin main", "deny"),
        ("git push origin feature/x", "allow"),
    ]:
        code, out, _ = run_hook("pretooluse-bash.sh",
                                {"tool_name": "Bash", "tool_input": {"command": cmd}}, repo)
        check(f"{cmd!r} -> {expected}", decision(code, out) == expected,
              f"got {decision(code, out)}")
    # And the default still holds where nothing is declared.
    repo = make_repo(tmp / "branches-default")
    code, out, _ = run_hook("pretooluse-bash.sh",
                            {"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}}, repo)
    check("default (main|master) survives an empty policy", decision(code, out) == "deny",
          f"got {decision(code, out)}")

    section("the gate survives backslashes in patterns (jq @tsv would corrupt these)")
    repo = make_repo(tmp / "esc", {
        "denyBashPatterns": [
            {"pattern": r"(^[[:space:]]*|\$\([[:space:]]*)railway +up",
             "reason": "escaped metacharacters must round-trip through the policy reader."}
        ]
    })
    code, out, _ = run_hook("pretooluse-bash.sh",
                            {"tool_name": "Bash", "tool_input": {"command": "railway up"}}, repo)
    check("a pattern containing \\$\\( still matches", decision(code, out) == "deny",
          f"got {decision(code, out)}")

    section("verify lints the LIVE policy for inert rules")
    problems = sc.lint_policy({"denyBashPatterns": [{"pattern": "${CSEP}x", "reason": "r"}]})
    check("an inert live rule is reported", bool(problems), str(problems))


def test_measure(tmp: Path) -> None:
    """`measure` is what makes an enforcement claim a measurement.

    The bar it has to clear is not "finds broken fences". It is "never calls a
    working fence broken, and never calls an unmeasured fence passing". The
    previous detector failed the first half on 9 rules, so most of these are
    negative tests about restraint.
    """
    sys.path.insert(0, str(KIT / "lib"))
    import measure as me

    section("measure — the ERE expander handles what mine_probes could not")
    nested = (r"(^[[:space:]]*|[;&|][[:space:]]*)(npx +|pnpm +(exec +|dlx +))?"
              r"sanity +(documents +(create|delete)|dataset +import)")
    got = me.expand_ere(nested)
    check("a nested optional group expands", bool(got), str(got[:2]))
    check("and every candidate really matches its own pattern",
          all(me._matches_all(c, [nested]) for c in got))

    negated = r"(^|[^[:alnum:]_$])(SANITY_(API_)?WRITE_TOKEN|SANITY_AUTH_TOKEN)="
    got = me.expand_ere(negated)
    check("a negated POSIX class expands", bool(got), str(got[:2]))
    check("and the negated char is genuinely excluded",
          all(me._matches_all(c, [negated]) for c in got))

    section("measure — a real conjunction is NOT called inert")
    # These three shipped as `allOf` and the shape heuristic called them broken.
    # `npm install -g x` satisfies all three at once, so they are real fences.
    real = [r"(^[[:space:]]*|[;&|][[:space:]]*)([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]+ +)*(npm|pnpm|yarn|bun)([^[:alnum:]_]|$)",
            r"(^|[[:space:]])(install|i|add)([[:space:]]|$)",
            r"(^|[[:space:]])(-g|--global)([[:space:]]|=|$)"]
    probes, note = me.compose_probes(real)
    check("a genuine multi-condition rule yields a composite probe", bool(probes), str(probes[:2]))
    check("and is NOT reported inert", note != me.INERT, note)

    section("measure — a contradiction IS called inert")
    # The `||`-read-as-`&&` shape: two verbs that cannot both be the command.
    contradiction = [r"(^|[[:space:]])vercel([[:space:]]|$)",
                     r"(^|[[:space:]])supabase([[:space:]]|$)"]
    probes, note = me.compose_probes(contradiction)
    # Only inert if nothing satisfies both. A composite "vercel supabase" would
    # satisfy both, so this asserts the honest outcome either way.
    if probes:
        check("a composite satisfying both is validated before use",
              all(me._matches_all(p, contradiction) for p in probes))
    else:
        check("mutually exclusive conditions are reported INERT", note == me.INERT, note)

    section("measure — an unbuildable probe is UNMEASURED, never passing")
    probes, note = me.compose_probes([r"(?<!x)impossible-lookbehind"])
    check("an unparseable pattern yields no probe", not probes)
    check("and is UNPROBEABLE, not INERT and not ENFORCED",
          note == me.UNPROBEABLE, note)
    check("UNPROBEABLE is not counted as a broken fence",
          me.UNPROBEABLE not in me.BROKEN)
    check("but NARROW is", me.NARROW in me.BROKEN)

    section("measure — probes never come from the must-always-work corpus")
    # A probe drawn from the benign set would assert a rule fires on a command the
    # same run asserts must always pass.
    overlap = set(me.BENIGN_FREE_CORPUS) & set(me.BENIGN_CORPUS)
    check("fallback corpus excludes every benign command", not overlap, str(overlap))

    section("measure — coverage families catch a pattern that is merely wrong")
    # Pattern-derived probes match by construction, so they prove a pattern is
    # LIVE and never that it is RIGHT. This is the independent check.
    check("checks-bypass family covers the natural argument order",
          "git commit --no-verify -m x" in me.COVERAGE_FAMILIES["checks-bypass"])
    check("and the order that a real repo's pattern matched instead",
          "git commit -m x --no-verify" in me.COVERAGE_FAMILIES["checks-bypass"])


def test_vendor_and_precommit(tmp: Path) -> None:
    """A repo has to be able to check itself, on any machine, with no network.

    Measured 2026-08-09: `kit/agentkit` was absent from all six migrated repos, so
    every one of them depended on the kit's source checkout existing at one path on one
    laptop, and nothing ran the checks on commit anywhere.
    """
    repo = tmp / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(list(a), cwd=repo, capture_output=True, text=True)
    run("git", "init", "-q", ".")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (repo / "README.md").write_text("# t\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")

    proc = subprocess.run([sys.executable, str(KIT / "agentkit"), "apply", "--repo", str(repo)],
                          capture_output=True, text=True)

    section("apply — the repo becomes self-sufficient")
    check("kit/agentkit is vendored", (repo / "kit" / "agentkit").exists())
    check("and it is executable", os.access(repo / "kit" / "agentkit", os.X_OK))
    check("the residue checker comes with it", (repo / "kit" / "verify" / "residue.py").exists())
    check("tests are NOT vendored (runtime, not workshop)",
          not (repo / "kit" / "tests").exists())

    section("apply — the check is wired to fire on commit")
    hook = repo / ".githooks" / "pre-commit"
    check("a pre-commit hook exists", hook.exists())
    check("it calls agentkit verify", "agentkit verify" in hook.read_text(encoding="utf-8"))
    check("core.hooksPath is set, or git would never run it",
          run("git", "config", "core.hooksPath").stdout.strip() == ".githooks")

    section("apply — re-running changes nothing (idempotent)")
    again = subprocess.run([sys.executable, str(KIT / "agentkit"), "apply", "--repo", str(repo)],
                           capture_output=True, text=True)
    check("second apply reports 0 changes", "0 change(s)" in again.stdout, again.stdout[-200:])

    section("pre-commit — blocks a real error, and cannot pass by not running")
    text = hook.read_text(encoding="utf-8")
    check("a missing python3 BLOCKS rather than skips",
          "python3 not found" in text and "exit 1" in text)
    check("a missing vendored kit BLOCKS rather than skips",
          "kit/agentkit is missing" in text)
    # An AGENTS.md over Codex's 32,768-byte cap is a BLOCKING finding at every stage.
    (repo / "AGENTS.md").write_text("# rules\n\n" + ("x" * 100 + "\n") * 400, encoding="utf-8")
    run("git", "add", "AGENTS.md")
    committed = run("git", "commit", "-m", "oversized")
    check("an over-budget AGENTS.md is refused", committed.returncode != 0)
    check("and the commit did NOT land",
          "oversized" not in run("git", "log", "--oneline").stdout)


def test_stale_session_tombstone(tmp: Path) -> None:
    """A hook referenced but not on disk must BLOCK, not wave the command through.

    Measured 2026-08-08: a migration rewired the hooks, a session already open kept
    calling the deleted script, the shell returned 127, Claude Code treats anything
    but 2 as non-blocking — and an entire session of commits ran with no Bash gate,
    announced only by a warning line that scrolled past.
    """
    repo = tmp / "repo"
    (repo / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(list(a), cwd=repo, capture_output=True, text=True)
    run("git", "init", "-q", ".")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")

    legacy = repo / ".claude" / "hooks" / "legacy-gate.sh"
    legacy.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    legacy.chmod(0o755)
    (repo / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command",
         "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/legacy-gate.sh"}]}]}}), encoding="utf-8")
    (repo / "README.md").write_text("# t\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")

    section("stale sessions — the interpreter is not read as the script")
    # A sweep that took the first token once deleted a live hook in a real repo.
    paths = agentkit_mod._hook_script_paths({"PreToolUse": [{"hooks": [
        {"command": "bash ${CLAUDE_PROJECT_DIR}/.claude/hooks/real.sh"}]}]}, repo)
    check("`bash <script>` resolves to the script, not to bash",
          paths == {repo / ".claude" / "hooks" / "real.sh"}, str(paths))

    section("stale sessions — a vanished hook is tombstoned, and fails CLOSED")
    legacy.unlink()          # exactly what retiring the old gate does
    proc = subprocess.run([sys.executable, str(KIT / "agentkit"), "apply",
                           "--repo", str(repo), "--allow-dirty"],
                          capture_output=True, text=True)
    check("apply tombstones the vanished hook", legacy.exists(), proc.stdout[-300:])
    check("and says the hooks changed", "RESTART" in proc.stdout.upper())

    fired = subprocess.run(["bash", str(legacy)],
                           input='{"tool_name":"Bash","tool_input":{"command":"rm -rf /"}}',
                           capture_output=True, text=True)
    check("the tombstone exits 2 (blocks) rather than 127 (proceeds)",
          fired.returncode == 2, f"exit {fired.returncode}")
    check("and tells the operator to restart", "RESTART" in fired.stderr.upper())

    section("stale sessions — a live hook is never tombstoned over")
    live = repo / ".claude" / "hooks" / "still-here.sh"
    live.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    before = live.read_bytes()
    agentkit_mod.tombstone_missing_hooks(repo, {"PreToolUse": [{"hooks": [
        {"command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/still-here.sh"}]}]}, {}, False)
    check("an existing script is left alone", live.read_bytes() == before)


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        test_bash_gate(tmp / "bash")
        test_write_gate(tmp / "write")
        test_mcp_gate(tmp)
        test_configchange(tmp)
        test_residue(tmp)
        test_policy_and_supersede(tmp / "policy")
        test_apply(tmp / "applytest")
        test_measure(tmp / "measure")
        test_vendor_and_precommit(tmp / "vendor")
        test_stale_session_tombstone(tmp / "stale")

    print(f"\n{'=' * 60}")
    print(f"agentkit tests: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
