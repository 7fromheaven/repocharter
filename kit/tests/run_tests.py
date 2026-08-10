#!/usr/bin/env python3
"""Dependency-free agentkit test suite: ``python3 kit/tests/run_tests.py``.

Negative cases exercise commands each gate must refuse, malformed inputs, and fail-closed
paths. Positive cases ensure the same policies do not block ordinary work.
"""

from __future__ import annotations

import contextlib
import io
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


def run_hook(name: str, payload, repo: Path,
             harness: str = "claude-code") -> tuple[int, str, str]:
    env = dict(os.environ)
    if harness == "codex":
        env.pop("CLAUDE_PROJECT_DIR", None)
        env["AGENTKIT_HARNESS"] = "codex"
        if isinstance(payload, dict) and "permission_mode" not in payload:
            payload = {**payload, "permission_mode": "default"}
    else:
        env["CLAUDE_PROJECT_DIR"] = str(repo)
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
        ("force-push after git -C",     "git -C /tmp/repo push --force origin main", "deny"),
        ("force-push inside shell script", "bash -c 'git push --force origin main'",   "deny"),
        ("--no-verify commit",          "git commit --no-verify -m 'x'",             "deny"),
        ("core.hooksPath bypass",       "git -c core.hooksPath=/dev/null push",      "deny"),
        ("plain push to main",          "git push origin main",                      "ask"),
        ("plain push after git -C",     "git -C /tmp/repo push origin main",         "ask"),
        ("reset --hard",                "git reset --hard HEAD~1",                   "ask"),
        ("git clean -fd",               "git clean -fd",                             "ask"),
        ("git clean -d -f separated",   "git clean -d -f",                           "ask"),
        ("git restore",                 "git restore src/app.ts",                    "ask"),
        ("git stash drop",              "git stash drop",                            "ask"),
        ("checkout a file",             "git checkout -- src/app.ts",                "ask"),
        ("recursive rm outside safe",   "rm -rf /Users/someone/important",           "ask"),
        ("recursive rm after real pipe", "printf safe | rm -rf /Users/someone/important", "ask"),
        ("recursive rm in command substitution",
         'printf "%s" "$(printf safe; rm -rf /Users/someone/important)"',            "ask"),
        ("recursive rm in backticks",
         'printf "%s" "`rm -rf /Users/someone/important`"',                         "ask"),
        ("recursive rm in single-quoted shell script",
         "bash -c 'printf safe; rm -rf /Users/someone/important'",                   "ask"),
        ("recursive rm in double-quoted shell script",
         'sh -c "printf safe; rm -rf /Users/someone/important"',                     "ask"),
        ("recursive rm at shell script start",
         "bash -c 'rm -rf /Users/someone/important'",                                "ask"),
        ("ask cannot hide later deny",  "rm -rf /Users/someone/important; wp db drop", "deny"),

        # Database wipes are denied; reset operations request confirmation because they can
        # be valid in local development.
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

    section("Bash gate — Codex hands confirmation to its native approval path")
    code, out, _ = run_hook(
        "pretooluse-bash.sh",
        {"tool_name": "Bash", "tool_input": {"command": "git reset --hard HEAD~1"}},
        repo,
        harness="codex",
    )
    check("approval-capable Codex does not hard-deny an ask", decision(code, out) == "allow", out[:240])
    check("the handoff requires native approval",
          "native permission request" in context_of(out), out[:240])

    code, out, _ = run_hook(
        "pretooluse-bash.sh",
        {"permission_mode": "bypassPermissions", "tool_name": "Bash",
         "tool_input": {"command": "git reset --hard HEAD~1"}},
        repo,
        harness="codex",
    )
    check("Codex without an approval path still denies an ask", decision(code, out) == "deny", out[:240])
    check("the denial names the unavailable approval path", "no active approval path" in out, out[:240])

    section("Bash gate — what it must NOT refuse")
    for label, command in [
        ("plain ls",                 "ls -la"),
        ("checkout -b is safe",      "git checkout -b feature/x"),
        ("push to a feature branch", "git push origin feature/x"),
        ("rm -rf in /tmp",           "rm -rf /tmp/scratch"),
        ("rm -rf a build dir",       "rm -rf ./dist/"),
        ("--no-verify inside a message body", "git commit -m 'note\nmentions --no-verify here'"),
        ("single-quoted regex separators",
         "rg -n 'safe|rm -r; safer && rm -r' ."),
        ("double-quoted regex separators",
         'rg -n "safe|rm -r; safer && rm -r" .'),
        ("escaped pipe is literal", r"printf '%s' safe\|rm -r"),
        ("quoted regex inside command substitution",
         'printf "%s" "$(rg -n \'safe|rm -r|safer\' .)"'),
        ("quoted regex inside backticks",
         'printf "%s" "`rg -n \'safe|rm -r|safer\' .`"'),
        ("quoted regex inside shell script",
         "bash -c \"rg -n 'safe|rm -r|safer' .\""),
        ("escaped double-quoted data inside shell script",
         'sh -c "printf \'%s\' \\"safe|rm -r|safer\\""'),

        # Database rules must not block searches for dangerous SQL or reads of migration
        # files; both require a database command in addition to the destructive verb.
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
    # A repository `ask` is held until universal denials have been evaluated.
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

    # Regression: before 0.3.1, a repo ask overlapping a universal ask made ask() return
    # 1 on the second match. `set -e` then exited before emitting either decision.
    overlap = make_repo(tmp / "overlap", policy={"askBashPatterns": [
        {"pattern": "git +reset +--hard", "reason": "repo also protects hard reset"}]})
    code, out, _ = run_hook(
        "pretooluse-bash.sh",
        {"tool_name": "Bash", "tool_input": {"command": "git reset --hard HEAD~1"}},
        overlap,
    )
    check("overlapping repo + universal asks still emit an ask",
          decision(code, out) == "ask", f"got {decision(code, out)}")

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
             "reason": "report a quantitative change for localized content."},
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

    section("apply_patch gate — Codex checks every path in a multi-file patch")
    patch = """*** Begin Patch
*** Update File: src/app.ts
@@
-old
+new
*** Add File: lib/content/live/new.json
+{}
*** End Patch"""
    code, out, _ = run_hook(
        "pretooluse-write.py",
        {"tool_name": "apply_patch", "tool_input": {"command": patch}},
        repo,
        harness="codex",
    )
    check("a forbidden second path denies the whole patch", decision(code, out) == "deny", out[:200])
    check("the Codex denial names the forbidden path", "new.json" in out, out[:200])

    safe_patch = """*** Begin Patch
*** Add File: src/new.ts
+export {};
*** End Patch"""
    code, out, _ = run_hook(
        "pretooluse-write.py",
        {"tool_name": "apply_patch", "tool_input": {"command": safe_patch}},
        repo,
        harness="codex",
    )
    check("an unrelated Codex patch is allowed", decision(code, out) == "allow", out[:160])

    config_patch = """*** Begin Patch
*** Update File: .codex/hooks.json
@@
-{}
+{"hooks": {}}
*** End Patch"""
    code, out, _ = run_hook(
        "pretooluse-write.py",
        {"tool_name": "apply_patch", "tool_input": {"command": config_patch}},
        repo,
        harness="codex",
    )
    check("Codex cannot apply_patch its own hook config", decision(code, out) == "deny", out[:200])

    code, _, err = run_hook(
        "pretooluse-write.py",
        {"tool_name": "apply_patch", "tool_input": {"command": "not a patch"}},
        repo,
        harness="codex",
    )
    check("an unrecognised Codex patch shape fails closed", code == 2, f"exit {code}: {err[:120]}")

    section("Edit/Write gate — configured before/after measurement")
    target = repo / "lib" / "content" / "loc.json"
    target.write_text(json.dumps({"title": "Привет мир"}, ensure_ascii=False), encoding="utf-8")
    code, out, _ = run_hook("pretooluse-write.py", {"tool_name": "Edit", "tool_input": {"file_path": str(target)}}, repo)
    ctx = context_of(out)
    check("before-measurement is taken", "BEFORE" in ctx and "Cyrillic" in ctx, ctx[:160])

    target.write_text(json.dumps({"title": "Привет"}, ensure_ascii=False), encoding="utf-8")
    code, out, _ = run_hook("posttooluse-write.py", {"tool_name": "Edit", "tool_input": {"file_path": str(target)}}, repo)
    ctx = context_of(out)
    check("after-measurement reports a delta", "AFTER" in ctx and "Cyrillic" in ctx, ctx[:200])
    check("a shrinking script count is flagged CHANGED", "CHANGED" in ctx, ctx[:200])

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

    code, _, err = run_hook("pretooluse-mcp.py", {"tool_input": {}}, repo, harness="codex")
    check("an MCP payload without a tool name fails closed", code == 2, f"exit {code}")


# ── the ConfigChange guard ────────────────────────────────────────────────────────────

def _entry(script: str) -> dict:
    return {"hooks": [{"type": "command", "command":
            f"${{CLAUDE_PROJECT_DIR}}/.claude/hooks/agentkit/{script}"}]}


def _healthy_settings() -> dict:
    return {
        "permissions": {"allow": [], "deny": ["Write(lib/content/live/**)"]},
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", **_entry("pretooluse-bash.sh")},
                {"matcher": "Edit|Write|NotebookEdit", **_entry("pretooluse-write.py")},
                {"matcher": "mcp__.*", **_entry("pretooluse-mcp.py")},
            ],
            "PostToolUse": [{"matcher": "Edit|Write|NotebookEdit", **_entry("posttooluse-write.py")}],
            "ConfigChange": [_entry("configchange-guard.py")],
        },
    }


def test_configchange(tmp: Path) -> None:
    section("ConfigChange guard — disarming the gates is loud")
    # The declaration must name denials, or the emptied-permissions.deny check has nothing
    # to protect and correctly stays quiet.
    repo = make_repo(tmp / "cfg", {"denyWritePaths": [{"glob": "lib/content/live/**", "reason": "r"}]})
    settings = repo / ".claude" / "settings.json"
    local = repo / ".claude" / "settings.local.json"

    def on_disk(data: dict, path: Path = settings) -> dict:
        """The payload the harness actually sends: a path, never the settings themselves."""
        path.write_text(json.dumps(data), encoding="utf-8")
        return {"hook_event_name": "ConfigChange", "source": "project_settings",
                "file_path": str(path)}

    code, _, err = run_hook("configchange-guard.py",
                            on_disk({**_healthy_settings(), "disableAllHooks": True}), repo)
    check("disableAllHooks refused", code == 2, f"exit {code}")
    check("and explains the blast radius", "every safety gate" in err, err[:120])

    dropped = _healthy_settings()
    dropped["permissions"]["deny"] = []
    code, _, err = run_hook("configchange-guard.py", on_disk(dropped), repo)
    check("emptying permissions.deny refused", code == 2, f"exit {code}")
    check("and names the declaration as the fix", "compatibility.json" in err, err[:200])

    missing = _healthy_settings()
    del missing["hooks"]["PreToolUse"]
    code, _, err = run_hook("configchange-guard.py", on_disk(missing), repo)
    check("dropping a hook event refused", code == 2, f"exit {code}")
    check("and names the missing event", "PreToolUse" in err, err[:200])

    noop = _healthy_settings()
    noop["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = "true"
    code, _, err = run_hook("configchange-guard.py", on_disk(noop), repo)
    check("replacing the Bash handler with a same-event no-op is refused", code == 2,
          f"exit {code}")
    check("and names the missing Bash wiring", "pretooluse-bash.sh" in err, err[:240])

    code, _, _ = run_hook("configchange-guard.py", on_disk(_healthy_settings()), repo)
    check("an innocuous settings change is allowed", code == 0, f"exit {code}")

    section("ConfigChange guard — the payload the harness really sends")
    # The regression this class of defect produced: the guard waited for a `new_settings`
    # key the harness never sends, so every branch below it ran against {} and refused
    # indiscriminately. Both directions are asserted against the real shape.
    settings.write_text(json.dumps(_healthy_settings()), encoding="utf-8")
    code, _, err = run_hook("configchange-guard.py", {
        "hook_event_name": "ConfigChange", "source": "project_settings",
        "file_path": str(settings)}, repo)
    check("a benign reload is not refused for lack of a settings payload", code == 0,
          f"exit {code}: {err[:160]}")

    code, _, err = run_hook("configchange-guard.py", {
        "hook_event_name": "ConfigChange", "source": "local_settings",
        "file_path": str(local)}, repo)
    check("an absent settings.local.json is allowed", code == 0, f"exit {code}")

    local.write_text(json.dumps({"disableAllHooks": True}), encoding="utf-8")
    code, _, err = run_hook("configchange-guard.py", {
        "hook_event_name": "ConfigChange", "source": "local_settings",
        "file_path": str(local)}, repo)
    check("disableAllHooks in settings.local.json refused", code == 2, f"exit {code}")

    local.write_text(json.dumps({"permissions": {"allow": ["Bash(ls:*)"]}}), encoding="utf-8")
    code, _, err = run_hook("configchange-guard.py", {
        "hook_event_name": "ConfigChange", "source": "local_settings",
        "file_path": str(local)}, repo)
    check("an ordinary local allow-list change is not refused", code == 0,
          f"exit {code}: {err[:160]}")

    # A user- or enterprise-scope settings.json carries neither this repo's hooks nor its
    # denials. Judging it by the project's baseline would refuse every machine's own config.
    outside = tmp / "cfg" / "elsewhere" / ".claude"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "settings.json").write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    code, _, err = run_hook("configchange-guard.py", {
        "hook_event_name": "ConfigChange", "source": "user_settings",
        "file_path": str(outside / "settings.json")}, repo)
    check("a settings.json outside the project is not judged by project rules", code == 0,
          f"exit {code}: {err[:160]}")

    section("ConfigChange guard — fails closed when it cannot tell")
    settings.write_text("{not json at all", encoding="utf-8")
    code, _, err = run_hook("configchange-guard.py", {
        "hook_event_name": "ConfigChange", "source": "project_settings",
        "file_path": str(settings)}, repo)
    check("an unparseable settings file refuses the reload", code == 2, f"exit {code}")

    code, _, err = run_hook("configchange-guard.py", {
        "hook_event_name": "ConfigChange", "source": "project_settings"}, repo)
    check("a payload naming neither path nor settings refuses", code == 2, f"exit {code}")

    settings.write_text(json.dumps(_healthy_settings()), encoding="utf-8")
    code, _, _ = run_hook("configchange-guard.py", {
        "hook_event_name": "ConfigChange", "source": "other",
        "file_path": str(repo / ".claude" / "unrelated.json")}, repo)
    check("an unwatched config file is allowed", code == 0, f"exit {code}")

    settings.unlink()
    code, _, err = run_hook("configchange-guard.py", {
        "hook_event_name": "ConfigChange", "source": "project_settings",
        "file_path": str(settings)}, repo)
    check("deleting the project settings refuses the reload", code == 2, f"exit {code}")


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
    # Migration findings are TODOs while a repo is mid-migration; defects that are invalid
    # at every stage remain errors.
    repo = make_repo(tmp / "r13")
    (repo / ".agent-docs").mkdir(exist_ok=True)
    rules = repo / ".claude" / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    (rules / "big.md").write_text("# unscoped\n", encoding="utf-8")
    rep = residue(repo)
    check("mid-migration repo reports stage 'mechanical'", rep.get("stage") == "mechanical", str(rep.get("stage")))
    check("unscoped rules are a TODO, not an error", has(rep, "todos", "paths"), str(rep)[:200])
    # An empty repository policy is valid because the universal floor remains active.
    check("an empty policy is a note, not a warning",
          has(rep, "notes", "policy is empty") and not has(rep, "warnings", "policy is EMPTY"),
          str(rep.get("warnings"))[:160])
    check("and it does not claim the gates enforce nothing",
          not has(rep, "notes", "enforce nothing") and not has(rep, "warnings", "enforce nothing"))
    check("it names the floor that IS active",
          has(rep, "notes", "universal floor is active"), str(rep.get("notes"))[:200])

    section("Residue verifier — declaration schema and adapter reach are real checks")
    repo = make_repo(tmp / "schema-unknown")
    compat_path = repo / ".agents" / "compatibility.json"
    compat = json.loads(compat_path.read_text(encoding="utf-8"))
    compat["inventedCompatibilityClaim"] = True
    compat_path.write_text(json.dumps(compat), encoding="utf-8")
    rep = residue(repo)
    check("an unknown declaration key is rejected by the checked-in schema",
          has(rep, "errors", "inventedCompatibilityClaim"), str(rep["errors"])[:240])

    repo = make_repo(tmp / "future-provider", compat_extra={
        "adapters": [{
            "harness": "future-agent",
            "kind": "manual-import",
            "note": "Read the canonical files directly until native discovery exists",
        }],
        "enforcement": {"claude-code": "advisory", "future-agent": "advisory"},
    })
    rep = residue(repo)
    check("a future provider can be declared without changing agentkit's schema",
          not has(rep, "errors", "future-agent"), str(rep["errors"])[:240])

    repo = make_repo(tmp / "broken-import", compat_extra={
        "agentkitVersion": "0.3.0",
        "adapters": [
            {"harness": "codex", "kind": "native"},
            {"harness": "claude-code", "kind": "import",
             "from": "CLAUDE.md", "to": "AGENTS.md"},
            {"harness": "claude-code", "kind": "relative-symlink",
             "from": ".claude/skills/<name>", "to": "../../.agents/skills/<name>"},
        ],
        "enforcement": {"claude-code": "advisory", "codex": "advisory"},
    })
    (repo / "CLAUDE.md").write_text("# does not reach AGENTS\n", encoding="utf-8")
    rep = residue(repo)
    check("a declared Claude import without @AGENTS.md is rejected",
          has(rep, "errors", "exact line '@AGENTS.md'"), str(rep["errors"])[:260])

    section("Residue verifier — provider-neutral validation commands")
    repo = make_repo(tmp / "validation-pass", compat_extra={
        "validation": {"commands": [{
            "name": "portable pass",
            "argv": [sys.executable, "-c", "raise SystemExit(0)"],
        }]},
    })
    rep = residue(repo)
    check("a passing declared validation is recorded",
          has(rep, "notes", "validation passed: portable pass"), str(rep["notes"])[:220])

    repo = make_repo(tmp / "validation-fail", compat_extra={
        "validation": {"commands": [{
            "name": "portable fail",
            "argv": [sys.executable, "-c", "raise SystemExit(7)"],
        }]},
    })
    rep = residue(repo)
    check("a failing declared validation blocks verification",
          has(rep, "errors", "portable fail") and has(rep, "errors", "exit 7"),
          str(rep["errors"])[:240])

    # Same defect, but the repo now claims to be finished: it becomes an error.
    repo = make_repo(tmp / "r14")
    rules = repo / ".claude" / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    (rules / "big.md").write_text("# unscoped\n", encoding="utf-8")
    (repo / "docs" / "project").mkdir(parents=True, exist_ok=True)
    rep = residue(repo)
    check("a repo claiming 'migrated' turns the same finding into an ERROR",
          rep.get("stage") == "migrated" and has(rep, "errors", "paths"), str(rep)[:220])

    section("Residue verifier — skills adapter and harness-written directories")

    repo = make_repo(tmp / "r9")
    skill = repo / ".agents" / "skills" / "demo"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\nbody\n", encoding="utf-8")
    claude_skills = repo / ".claude" / "skills"
    claude_skills.mkdir(parents=True, exist_ok=True)
    (claude_skills / "demo").symlink_to(os.path.relpath(skill, claude_skills))
    rep = residue(repo)
    check("a conforming relative symlink passes", not has(rep, "errors", "demo"), str(rep["errors"])[:200])

    # Claude Code writes a real directory here when /verify runs; it is not an adapter error.
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
    check("Codex hooks.json installed", (repo / ".codex" / "hooks.json").exists())
    check("CLAUDE.md is a relative symlink, not an @-import",
          (repo / "CLAUDE.md").is_symlink()
          and not os.path.isabs(os.readlink(repo / "CLAUDE.md")),
          os.readlink(repo / "CLAUDE.md") if (repo / "CLAUDE.md").is_symlink() else "not a symlink")
    check("AGENTS.md untouched by apply", (repo / "AGENTS.md").read_bytes() == original)

    settings = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    matchers = [g.get("matcher") for g in settings["hooks"]["PreToolUse"]]
    check("three PreToolUse matchers, not one", len(matchers) == 3, str(matchers))
    check("Edit|Write matcher present", any("Edit" in (m or "") for m in matchers), str(matchers))
    check("mcp matcher present", any("mcp" in (m or "") for m in matchers), str(matchers))
    check("ConfigChange hook present", "ConfigChange" in settings["hooks"])

    codex_hooks = json.loads((repo / ".codex" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    codex_pre = codex_hooks["PreToolUse"]
    codex_matchers = [group.get("matcher") for group in codex_pre]
    check("Codex has Bash, apply_patch, and MCP matchers",
          codex_matchers == ["^Bash$", "^apply_patch$", "^mcp__.*$"], str(codex_matchers))
    codex_commands = [handler["command"] for groups in codex_hooks.values()
                      for group in groups for handler in group.get("hooks", [])]
    check("every Codex command selects Codex wire semantics",
          all("AGENTKIT_HARNESS=codex" in command for command in codex_commands),
          str(codex_commands))
    check("Codex post-write measurement is installed",
          any("posttooluse-write.py" in command for command in codex_commands),
          str(codex_commands))

    second = subprocess.run([sys.executable, str(KIT / "agentkit"), "apply", "--repo", str(repo)],
                            capture_output=True, text=True, timeout=120)
    check("second apply is idempotent", "0 change(s)" in second.stdout, second.stdout[-200:])

    section("apply — must NOT disarm a repo that already has hooks")
    # Applying agentkit must merge hook groups instead of replacing foreign entries.
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

    section("apply — must preserve foreign Codex hooks too")
    codex_config_path = repo / ".codex" / "hooks.json"
    codex_config = json.loads(codex_config_path.read_text(encoding="utf-8"))
    codex_config["hooks"].setdefault("SessionStart", []).append({
        "matcher": "startup",
        "hooks": [{"type": "command", "command": "python3 tools/their-codex-start.py"}],
    })
    codex_config["customUserKey"] = "kept"
    codex_config_path.write_text(json.dumps(codex_config, indent=2) + "\n", encoding="utf-8")
    codex_apply = subprocess.run(
        [sys.executable, str(KIT / "agentkit"), "apply", "--repo", str(repo)],
        capture_output=True, text=True, timeout=120,
    )
    after_codex = json.loads(codex_config_path.read_text(encoding="utf-8"))
    check("foreign Codex SessionStart hook survives",
          "SessionStart" in after_codex["hooks"], str(after_codex["hooks"]))
    check("foreign Codex top-level key survives", after_codex.get("customUserKey") == "kept")
    check("apply reports preserved Codex wiring", "preserved 1 existing Codex hook entry" in codex_apply.stdout,
          codex_apply.stdout[:400])

    legacy_codex = after_codex
    legacy_codex["hooks"]["PreToolUse"].append({
        "matcher": "Bash",
        "command": ["${CODEX_PROJECT_DIR}/.claude/hooks/agentkit/pretooluse-bash.sh"],
    })
    codex_config_path.write_text(json.dumps(legacy_codex, indent=2) + "\n", encoding="utf-8")
    legacy_upgrade = subprocess.run(
        [sys.executable, str(KIT / "agentkit"), "apply", "--repo", str(repo)],
        capture_output=True, text=True, timeout=120,
    )
    upgraded_codex = json.loads(codex_config_path.read_text(encoding="utf-8"))
    pre_groups = upgraded_codex["hooks"]["PreToolUse"]
    check("apply removes agentkit's obsolete Codex command-array shape",
          not any("command" in group for group in pre_groups), str(pre_groups))
    check("legacy Codex migration leaves exactly one current Bash group",
          legacy_upgrade.returncode == 0
          and len([group for group in pre_groups if group.get("matcher") == "^Bash$"]) == 1,
          legacy_upgrade.stderr + str(pre_groups))

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
    # A failed restore must leave the quarantine intact.
    stale = tmp / "stale-quarantine"
    stale.mkdir(parents=True, exist_ok=True)
    git_init(stale)
    (stale / "AGENTS.md").write_text("# t\n", encoding="utf-8")
    qdir = Path(os.environ["AGENTKIT_STATE_DIR"]) / "quarantine" / stale.name / "20200101T000000Z"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "manifest.json").write_text(json.dumps({"stamp": "x", "actions": [
        {"op": "rename", "from": "nowhere/absent.sh", "to": ".claude/hooks/absent.sh"}]}), encoding="utf-8")
    (qdir / "precious.txt").write_text("the only copy", encoding="utf-8")
    r = subprocess.run([sys.executable, str(KIT / "agentkit"), "revert", "--repo", str(stale)],
                       capture_output=True, text=True, timeout=60)
    check("revert exits non-zero when it restored nothing", r.returncode == 1, f"exit {r.returncode}")
    check("and the quarantine SURVIVES", (qdir / "precious.txt").exists())
    check("and it says where the content is", str(qdir) in r.stderr, r.stderr[:200])
    shutil.rmtree(Path(os.environ["AGENTKIT_STATE_DIR"]) / "quarantine" / stale.name,
                  ignore_errors=True)

    section("self-test — the thing that entitles a `blocking` claim")
    st = subprocess.run([sys.executable, str(KIT / "agentkit"), "self-test", "--repo", str(repo)],
                        capture_output=True, text=True, timeout=120)
    check("self-test passes on a fresh install", st.returncode == 0, st.stdout[-400:])
    check("self-test proves fail-closed", "fails CLOSED" in st.stdout, st.stdout[-200:])


def _stub_claude(bin_dir: Path, version: str) -> Path:
    """A fake `claude --version` on PATH, so version drift is testable without an upgrade."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "claude"
    stub.write_text(f'#!/bin/sh\n[ "$1" = "--version" ] && echo "{version}" && exit 0\nexit 1\n',
                    encoding="utf-8")
    stub.chmod(0o755)
    return stub


def _stub_codex(bin_dir: Path, version: str) -> Path:
    """A fake `codex --version` for checkout-attestation verifier tests."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "codex"
    stub.write_text(f'#!/bin/sh\n[ "$1" = "--version" ] && echo "{version}" && exit 0\nexit 1\n',
                    encoding="utf-8")
    stub.chmod(0o755)
    return stub


def test_claude_enforcement_evidence(tmp: Path) -> None:
    """A `blocking` Claude claim is a claim about bytes and a version. Both must be checked."""
    root = tmp / "claude-evidence"
    repo = make_repo(root)
    git_init(repo)
    (repo / "CLAUDE.md").unlink()  # apply installs the shim; a stale @-import would be refused
    installed = subprocess.run([sys.executable, str(KIT / "agentkit"), "apply", "--repo", str(repo),
                                "--allow-dirty"], capture_output=True, text=True, timeout=120)
    check("the fixture installs cleanly before anything is claimed about it",
          installed.returncode == 0, installed.stdout[-300:] + installed.stderr[-300:])
    compat_path = repo / ".agents" / "compatibility.json"

    def declare(enforcement: str, evidence=None) -> None:
        compat = json.loads(compat_path.read_text(encoding="utf-8"))
        compat.setdefault("enforcement", {})["claude-code"] = enforcement
        if evidence is None:
            compat.pop("enforcementEvidence", None)
        else:
            compat["enforcementEvidence"] = {"claude-code": evidence}
        compat_path.write_text(json.dumps(compat, indent=2), encoding="utf-8")

    git_bin = str(Path(shutil.which("git") or "/usr/bin/git").parent)
    base_test_path = os.pathsep.join(dict.fromkeys((git_bin, "/usr/bin", "/bin")))
    claude_config = tmp / "claude-config"
    claude_config.mkdir()
    Path(f"{claude_config}.json").write_text(json.dumps({"projects": {
        str(repo.parent): {"hasTrustDialogAccepted": True},
    }}), encoding="utf-8")

    def report_with(path_env: Path | None = None, extra_env: dict | None = None) -> dict:
        env = dict(os.environ)
        if path_env is not None:
            env["PATH"] = str(path_env) + os.pathsep + base_test_path
        env["CLAUDE_CONFIG_DIR"] = str(claude_config)
        env.update(extra_env or {})
        proc = subprocess.run([sys.executable, str(RESIDUE), "--repo", str(repo), "--json"],
                              capture_output=True, text=True, timeout=60, env=env)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"errors": [f"residue crashed: {proc.stderr[:300]}"], "notes": []}

    def errors_with(fragment: str, path_env: Path | None = None,
                    extra_env: dict | None = None) -> bool:
        report = report_with(path_env, extra_env)
        return any(fragment in e for e in report.get("errors", []))

    section("Claude promotion — a blocking claim without live evidence is refused")
    declare("blocking")
    check("blocking with no enforcementEvidence is an error",
          errors_with("no live enforcementEvidence"))
    declare("advisory")
    check("the same checkout is clean while it stays advisory",
          not errors_with("enforcementEvidence"))

    section("Claude promotion — evidence is bound to the adapter bytes")
    true_digest = agentkit_mod.claude_adapter_digest(repo)
    check("the digest covers settings.json and every script it invokes",
          isinstance(true_digest, str) and len(true_digest) == 64, str(true_digest))
    good = {"verifiedOn": "2026-08-10", "harnessVersion": "9.9.9 (Claude Code)",
            "adapterSha256": true_digest, "method": "live-deny-and-observe"}

    declare("blocking", {**good, "adapterSha256": "0" * 64})
    check("a hash that does not match the installed adapter is stale",
          errors_with("Claude blocking evidence is stale"))

    stub_bin = tmp / "stubbin"
    _stub_claude(stub_bin, "9.9.9 (Claude Code)")
    declare("blocking", good)
    check("portable evidence alone does not certify this checkout",
          errors_with("no matching local attestation", stub_bin))
    local_evidence = agentkit_mod.checkout_evidence_path(repo, "claude-code")
    check("the local record lives in this checkout's private Git directory",
          local_evidence is not None and repo.resolve() / ".git" in local_evidence.parents,
          str(local_evidence))
    check("a matching checkout-local record can be written",
          agentkit_mod.write_checkout_evidence(repo, "claude-code", good))
    check("matching repository bytes, version, trust, and local attestation verify clean",
          not errors_with("Claude blocking evidence", stub_bin)
          and not errors_with("attestation", stub_bin)
          and not errors_with("workspace trust", stub_bin))

    local_original = local_evidence.read_text(encoding="utf-8")
    local_data = json.loads(local_original)
    local_data["checkout"] = str(repo.parent / "some-other-clone")
    local_evidence.write_text(json.dumps(local_data), encoding="utf-8")
    check("a copied or path-mismatched local attestation is rejected",
          errors_with("belongs to another checkout", stub_bin))
    local_evidence.write_text(local_original, encoding="utf-8")

    Path(f"{claude_config}.json").write_text(json.dumps({"projects": {}}), encoding="utf-8")
    check("provider output cannot substitute for persisted interactive trust",
          errors_with("no persisted workspace trust", stub_bin))
    Path(f"{claude_config}.json").write_text(json.dumps({"projects": {
        str(repo.parent): {"hasTrustDialogAccepted": True},
    }}), encoding="utf-8")

    (claude_config / "settings.json").write_text(
        json.dumps({"disableAllHooks": True}), encoding="utf-8")
    check("a user-level disableAllHooks invalidates blocking evidence",
          errors_with("sets disableAllHooks", stub_bin))
    (claude_config / "settings.json").write_text("{}", encoding="utf-8")
    check("Claude safe mode invalidates blocking evidence",
          errors_with("CLAUDE_CODE_SAFE_MODE", stub_bin, {"CLAUDE_CODE_SAFE_MODE": "1"}))

    guard = repo / ".claude" / "hooks" / "agentkit" / "configchange-guard.py"
    original = guard.read_text(encoding="utf-8")
    guard.write_text(original + "# one byte of drift\n", encoding="utf-8")
    check("editing a hook script the adapter invokes invalidates the evidence",
          errors_with("Claude blocking evidence is stale"))
    guard.write_text(original, encoding="utf-8")

    settings_path = repo / ".claude" / "settings.json"
    settings_original = settings_path.read_text(encoding="utf-8")
    settings = json.loads(settings_original)
    settings["permissions"]["allow"] = ["Bash(ls:*)"]
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    check("editing settings.json invalidates the evidence too",
          errors_with("Claude blocking evidence is stale"))
    settings_path.write_text(settings_original, encoding="utf-8")

    section("Claude promotion — evidence is bound to the harness version")
    _stub_claude(stub_bin, "9.9.10 (Claude Code)")
    check("a different installed Claude version invalidates the evidence",
          errors_with("but this machine runs", stub_bin))
    _stub_claude(stub_bin, "9.9.9 (Claude Code)")
    check("the matching version does not", not errors_with("but this machine runs", stub_bin))

    section("Claude promotion — a missing Claude cannot be silently passed")
    empty_bin = tmp / "emptybin"
    empty_bin.mkdir(parents=True, exist_ok=True)
    local_evidence.unlink()
    check("verify degrades to a note rather than a false pass",
          not errors_with("but this machine runs", empty_bin))
    missing_report = report_with(empty_bin)
    check("offline verification does not demand machine-local proof without the provider",
          not any("attestation" in error for error in missing_report.get("errors", [])),
          str(missing_report.get("errors", []))[:300])
    env = dict(os.environ, PATH=str(empty_bin) + os.pathsep + base_test_path,
               CLAUDE_CONFIG_DIR=str(claude_config))
    declare("advisory")
    st = subprocess.run([sys.executable, str(KIT / "agentkit"), "self-test", "--repo", str(repo),
                         "--promote-claude"], capture_output=True, text=True, timeout=180, env=env)
    check("self-test --promote-claude fails when `claude` is absent", st.returncode == 1,
          st.stdout[-300:])
    check("and says the executable is unavailable", "executable is unavailable" in st.stdout,
          st.stdout[-300:])
    after = json.loads(compat_path.read_text(encoding="utf-8"))
    check("a failed live run PROMOTES NOTHING",
          after["enforcement"]["claude-code"] == "advisory",
          after["enforcement"]["claude-code"])
    check("and writes no evidence",
          "claude-code" not in (after.get("enforcementEvidence") or {}))

    section("Claude promotion — the probe cannot be satisfied by a native denial")
    argv = agentkit_mod._claude_probe_command(repo, "x", repo / "mcp.json", "Bash")
    check("--bare is never used, because it skips hooks", "--bare" not in argv, " ".join(argv))
    check("hook lifecycle events are streamed", "--include-hook-events" in argv)
    check("project and local settings are the only sources",
          "project,local" in argv, " ".join(argv))
    check("only the intended probe tool is exposed and pre-allowed",
          argv[argv.index("--tools") + 1] == "Bash"
          and argv[argv.index("--allowedTools") + 1] == "Bash"
          and "Read" not in argv, " ".join(argv))
    target_argv = agentkit_mod._claude_checkout_probe_command(repo, repo / "mcp.json")
    check("the target probe includes user, project, and local settings",
          "user,project,local" in target_argv, " ".join(target_argv))
    check("the target probe uses the harmless hooksPath bypass signature",
          any("git -c core.hooksPath=/dev/null status" in part for part in target_argv),
          " ".join(target_argv))
    probe_settings = agentkit_mod.claude_probe_settings(
        {"permissions": {"deny": ["Write(blocked.txt)"], "allow": []}})
    check("the probe repo empties permissions.deny",
          probe_settings["permissions"]["deny"] == [])

    # The matrix is unpacked positionally by the live loop, so an added or dropped field is
    # a crash mid-run rather than a failed check. Assert its shape without a model call.
    probes = agentkit_mod.claude_probes(repo, repo / "mcp-call-ran.txt")
    check("every live probe carries exactly the six fields the runner unpacks",
          all(len(p) == 6 for p in probes), str([len(p) for p in probes]))
    check("every probe names a marker path outside the transcript",
          all(isinstance(p[4], Path) for p in probes))
    check("the matrix proves refusals AND that ordinary work still runs",
          {p[5] for p in probes} == {True, False},
          str(sorted(p[0] for p in probes)))
    check("each denial requires its own RepoCharter reason",
          all(p[3] for p in probes if p[5] is False),
          str([p[0] for p in probes if p[5] is False and not p[3]]))
    check("Bash, Write and MCP are each covered in both directions",
          all(sum(1 for p in probes if surface in p[0]) == 2
              for surface in ("Bash", "Write", "MCP")),
          str([p[0] for p in probes]))

    def stream(tool: str = "Bash", pre: dict | None = None,
               post: dict | None = None, extra: list[dict] | None = None) -> str:
        events = [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": tool, "id": "toolu_probe", "input": {}},
        ]}}]
        if pre is not None:
            events.append({"type": "system", "subtype": "hook_response",
                           "hook_event": "PreToolUse", "hook_name": f"PreToolUse:{tool}",
                           "exit_code": 0, "outcome": "success", **pre})
        if post is not None:
            events.append({"type": "system", "subtype": "hook_response",
                           "hook_event": "PostToolUse", "hook_name": f"PostToolUse:{tool}",
                           "exit_code": 0, "outcome": "success", **post})
        events.extend(extra or [])
        return "\n".join(json.dumps(event) for event in events)

    deny_output = json.dumps({"hookSpecificOutput": {
        "permissionDecision": "deny",
        "permissionDecisionReason": "Repo policy: AGENTKIT_X_DENIED",
    }})
    measurement_output = json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": "[agentkit measurement — AFTER] measured.txt: bytes",
    }})
    held = agentkit_mod.claude_probe_held
    check("prose containing a reason but no structured tool call is not evidence",
          not held("Bash", "AGENTKIT_X_DENIED", "AGENTKIT_X_DENIED", False, False))
    check("a native denial without a project hook response is AMBIGUOUS, not a pass",
          not held("Bash", "AGENTKIT_X_DENIED", stream(extra=[{
              "type": "system", "subtype": "permission_denied", "tool_name": "Bash",
              "message": "Claude requested permissions, but you haven't granted it yet.",
          }]), False, False))
    check("a response for the wrong intended tool is not evidence",
          not held("Bash", "AGENTKIT_X_DENIED",
                   stream("Write", {"output": deny_output}), False, False))
    check("a RepoCharter reason whose marker landed anyway is a FAILURE",
          not held("Bash", "AGENTKIT_X_DENIED",
                   stream(pre={"output": deny_output}), True, False))
    check("a structured project-hook denial passes",
          held("Bash", "AGENTKIT_X_DENIED",
               stream(pre={"output": deny_output}), False, False))
    check("an allow whose side effect never landed is a FAILURE",
          not held("Bash", "", stream(pre={"output": ""}), False, True))
    check("an allow with a successful structured response and side effect passes",
          held("Bash", "", stream(pre={"output": ""}), True, True))
    check("an allow that must also show a measurement fails without it",
          not held("Write", "AFTER] measured.txt",
                   stream("Write", {"output": ""}), True, True))
    check("and passes with it",
          held("Write", "AFTER] measured.txt",
               stream("Write", {"output": ""}, {"output": measurement_output}), True, True))

    section("Claude promotion — an installed guard that allows everything fails self-test")
    # Headless Claude emitted no ConfigChange event for authorized settings mutations. Its
    # provider-shaped fixture therefore has to be able to fail rather than blessing a no-op.
    guard_path = repo / ".claude" / "hooks" / "agentkit" / "configchange-guard.py"
    guard_original = guard_path.read_text(encoding="utf-8")

    def fixture_failures() -> int:
        # The checker narrates each case on stdout. A deliberately broken guard would
        # therefore print FAIL lines into a passing suite, which is exactly the kind of
        # log nobody can read twice.
        with contextlib.redirect_stdout(io.StringIO()):
            return agentkit_mod.configchange_fixture_checks(repo)

    check("a healthy installed guard passes the fixture", fixture_failures() == 0)
    guard_path.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8")
    guard_path.chmod(0o755)
    check("a guard that allows every settings change fails all four refusals",
          fixture_failures() == 4)
    guard_path.unlink()
    check("a missing guard is a failure, not a skip", fixture_failures() == 1)
    guard_path.write_text(guard_original, encoding="utf-8")
    guard_path.chmod(0o755)

    section("Claude promotion — a disarmed checkout cannot verify")
    declare("advisory")
    (repo / ".claude" / "settings.local.json").write_text(
        json.dumps({"disableAllHooks": True}), encoding="utf-8")
    check("disableAllHooks is an error even while Claude is only advisory",
          errors_with("disableAllHooks"))
    (repo / ".claude" / "settings.local.json").unlink()

    section("Codex promotion — portable evidence also needs checkout-local proof")
    compat = json.loads(compat_path.read_text(encoding="utf-8"))
    codex_good = {
        "verifiedOn": "2026-08-10",
        "harnessVersion": "codex-cli 9.9.9",
        "adapterSha256": agentkit_mod.codex_adapter_digest(repo),
        "method": "live-deny-and-observe",
    }
    compat.setdefault("enforcement", {})["codex"] = "blocking"
    compat.setdefault("enforcementEvidence", {})["codex"] = codex_good
    compat_path.write_text(json.dumps(compat, indent=2), encoding="utf-8")
    codex_bin = tmp / "codex-stubbin"
    _stub_codex(codex_bin, "codex-cli 9.9.9")
    check("Codex portable evidence alone is rejected in a fresh checkout",
          errors_with("codex is declared blocking but this checkout has no matching local",
                      codex_bin))
    check("writing the matching Codex checkout record satisfies that requirement",
          agentkit_mod.write_checkout_evidence(repo, "codex", codex_good)
          and not errors_with("codex checkout-local attestation", codex_bin)
          and not errors_with("codex is declared blocking but this checkout", codex_bin))


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
    # A bare branch-name matcher is over-broad without the git-push condition.
    branch_only = "(^|[^[:alnum:]_])main([^[:alnum:]_]|$)|(^|[^[:alnum:]_])master([^[:alnum:]_]|$)"
    blocked = sc.over_broad([branch_only])
    check("a bare branch matcher is caught as over-broad", "ls main.py" in blocked, str(blocked))
    check("and the real conjunction is NOT",
          sc.over_broad(["(^|[[:space:]])git +push", branch_only]) == [],
          str(sc.over_broad(["(^|[[:space:]])git +push", branch_only])))

    real_install = [
        r"(^[[:space:]]*|[;&|][[:space:]]*)(npm|pnpm|yarn|bun)([^[:alnum:]_]|$)",
        r"(^|[[:space:]])(install|i|add)([[:space:]]|$)",
        r"(^|[[:space:]])(-g|--global)([[:space:]]|=|$)",
    ]
    check("the verifier accepts a satisfiable multi-token conjunction",
          not sc.unsatisfiable(real_install))

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
    # The gate must use the declared branch list rather than hardcoding main and master.
    repo = make_repo(tmp / "branches", {"protectedBranches": ["main", "release"]})
    for cmd, expected in [
        ("git push --force origin release", "deny"),
        ("git push origin release", "ask"),
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
    """Test live policy measurement, including inconclusive and negative cases."""
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
    # `npm install -g x` satisfies this valid three-part conjunction.
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
    # Only inert if nothing satisfies both. A validated composite is also a correct result.
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

    section("measure — MCP regex alternatives are exercised, not left unmeasured")
    mcp_rule = {
        "pattern": r"^mcp__.*__(promote|update_project_deployment_protection)$",
        "reason": "both production mutation tools are denied",
    }
    repo = make_repo(tmp / "mcp-alternation", {"denyMcpTools": [mcp_rule]})
    measured = me.measure_mcp_rule(
        repo=repo,
        gate=KIT / "hooks" / "claude" / "pretooluse-mcp.py",
        rule=mcp_rule,
    )
    check("an alternation-based MCP rule is measured", measured["verdict"] == me.ENFORCED,
          str(measured))

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
    check("and the alternate argument order",
          "git commit -m x --no-verify" in me.COVERAGE_FAMILIES["checks-bypass"])


def test_vendor_and_precommit(tmp: Path) -> None:
    """A fresh clone can run vendored verification without a network or source checkout."""
    repo = tmp / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(list(a), cwd=repo, capture_output=True, text=True)
    run("git", "init", "-q", ".")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (repo / "README.md").write_text("# t\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
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

    section("pre-commit — runs provider-neutral declared validation")
    compat_path = repo / ".agents" / "compatibility.json"
    compat = json.loads(compat_path.read_text(encoding="utf-8"))
    compat["validation"] = {"commands": [{
        "name": "fixture clean-code failure",
        "argv": [sys.executable, "-c", "raise SystemExit(9)"],
    }]}
    compat_path.write_text(json.dumps(compat, indent=2) + "\n", encoding="utf-8")
    run("git", "add", ".agents/compatibility.json")
    rejected = run("git", "commit", "-m", "must not land")
    check("a failed declared validation blocks the commit", rejected.returncode != 0,
          rejected.stdout[-240:] + rejected.stderr[-240:])
    check("the validation failure is named", "fixture clean-code failure" in
          (rejected.stdout + rejected.stderr), (rejected.stdout + rejected.stderr)[-300:])

    compat["validation"] = {"commands": []}
    compat_path.write_text(json.dumps(compat, indent=2) + "\n", encoding="utf-8")
    run("git", "add", ".agents/compatibility.json")
    restored = run("git", "commit", "-m", "restore passing validation")
    check("the same gate permits a passing declared validation", restored.returncode == 0,
          restored.stdout[-240:] + restored.stderr[-240:])

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

    section("pre-commit — repairs an existing hook whose terminal exit hid the gate")
    legacy = tmp / "legacy-hook"
    legacy.mkdir(parents=True, exist_ok=True)
    legacy_run = lambda *a: subprocess.run(list(a), cwd=legacy, capture_output=True, text=True)
    legacy_run("git", "init", "-q", ".")
    legacy_run("git", "config", "user.email", "t@t")
    legacy_run("git", "config", "user.name", "t")
    (legacy / "README.md").write_text("# legacy hook fixture\n", encoding="utf-8")
    (legacy / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
    legacy_run("git", "add", "-A")
    legacy_run("git", "commit", "-qm", "init")
    hooks = legacy / ".githooks"
    hooks.mkdir()
    legacy_hook = hooks / "pre-commit"
    legacy_hook.write_text(
        "#!/usr/bin/env bash\n"
        "# Existing repository gate.\n"
        "set -u\n\n"
        "printf 'legacy-ran\\n' >> .legacy-hook-ran\n"
        "rc=0\n"
        "exit \"$rc\"\n",
        encoding="utf-8",
    )
    legacy_hook.chmod(0o755)
    legacy_run("git", "config", "core.hooksPath", ".githooks")

    installed = subprocess.run(
        [sys.executable, str(KIT / "agentkit"), "apply", "--repo", str(legacy)],
        capture_output=True, text=True,
    )
    installed_text = legacy_hook.read_text(encoding="utf-8")
    check("apply succeeds for an existing terminal-exit hook", installed.returncode == 0,
          installed.stdout[-300:] + installed.stderr[-300:])
    check("the managed block is moved before the legacy hook can exit",
          installed_text.index("# >>> agentkit >>>") < installed_text.index('exit "$rc"'))

    legacy_compat = legacy / ".agents" / "compatibility.json"
    declared = json.loads(legacy_compat.read_text(encoding="utf-8"))
    declared["validation"] = {"commands": [{
        "name": "terminal-exit fixture failure",
        "argv": [sys.executable, "-c", "raise SystemExit(9)"],
    }]}
    legacy_compat.write_text(json.dumps(declared, indent=2) + "\n", encoding="utf-8")
    legacy_run("git", "add", ".agents/compatibility.json")
    blocked = legacy_run("git", "commit", "-m", "must not pass terminal exit")
    check("a real commit reaches the managed validation before the terminal exit",
          blocked.returncode != 0, blocked.stdout[-300:] + blocked.stderr[-300:])
    check("the reached failure names the declared validation",
          "terminal-exit fixture failure" in blocked.stdout + blocked.stderr)
    check("the legacy hook body does not run after RepoCharter blocks",
          not (legacy / ".legacy-hook-ran").exists())

    declared["validation"] = {"commands": []}
    legacy_compat.write_text(json.dumps(declared, indent=2) + "\n", encoding="utf-8")
    legacy_run("git", "add", ".agents/compatibility.json")
    passed = legacy_run("git", "commit", "-m", "passing terminal-exit hook")
    check("a passing managed gate falls through to the existing hook", passed.returncode == 0,
          passed.stdout[-300:] + passed.stderr[-300:])
    check("the preserved existing hook actually ran", (legacy / ".legacy-hook-ran").exists())

    # Recreate the exact defect: a complete managed block is present, but a terminal
    # exit appears first. Manual verification must reject that false installation.
    current = legacy_hook.read_text(encoding="utf-8")
    start = current.index("# >>> agentkit >>>")
    finish = current.index("# <<< agentkit <<<") + len("# <<< agentkit <<<")
    lane = current[start:finish]
    broken = (current[:start] + current[finish:]).rstrip() + "\n\n" + lane + "\n"
    legacy_hook.write_text(broken, encoding="utf-8")
    rejected_wiring = legacy_run(sys.executable, "kit/agentkit", "verify", "--repo", ".")
    check("manual verify rejects a present-but-unreachable pre-commit block",
          rejected_wiring.returncode != 0,
          rejected_wiring.stdout[-400:] + rejected_wiring.stderr[-400:])
    check("the wiring error explains that an earlier exit hides the block",
          "unreachable" in (rejected_wiring.stdout + rejected_wiring.stderr).lower())

    repaired = subprocess.run(
        [sys.executable, str(KIT / "agentkit"), "apply", "--repo", str(legacy),
         "--allow-dirty"], capture_output=True, text=True,
    )
    repaired_text = legacy_hook.read_text(encoding="utf-8")
    check("re-apply repairs the unreachable managed block", repaired.returncode == 0 and
          repaired_text.index("# >>> agentkit >>>") < repaired_text.index('exit "$rc"') and
          repaired_text.endswith("\n") and not repaired_text.endswith("\n\n"),
          repaired.stdout[-300:] + repaired.stderr[-300:])
    again = subprocess.run(
        [sys.executable, str(KIT / "agentkit"), "apply", "--repo", str(legacy),
         "--allow-dirty"], capture_output=True, text=True,
    )
    check("the repaired existing hook is idempotent", "0 change(s)" in again.stdout,
          again.stdout[-300:])


def test_stale_session_tombstone(tmp: Path) -> None:
    """A hook referenced by a stale session must block after its implementation moves."""
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
    # Interpreter-prefixed hook commands must resolve to the script rather than the binary.
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
        # Keep quarantine/revert tests inside the disposable tree. This makes the same
        # dependency-free suite runnable from Codex's workspace sandbox and from CI,
        # instead of requiring write access to the operator's home directory.
        previous_state = os.environ.get("AGENTKIT_STATE_DIR")
        os.environ["AGENTKIT_STATE_DIR"] = str(tmp / "agentkit-state")
        test_bash_gate(tmp / "bash")
        test_write_gate(tmp / "write")
        test_mcp_gate(tmp)
        test_configchange(tmp)
        test_residue(tmp)
        test_claude_enforcement_evidence(tmp / "claude-enforcement")
        test_policy_and_supersede(tmp / "policy")
        test_apply(tmp / "applytest")
        test_measure(tmp / "measure")
        test_vendor_and_precommit(tmp / "vendor")
        test_stale_session_tombstone(tmp / "stale")
        if previous_state is None:
            os.environ.pop("AGENTKIT_STATE_DIR", None)
        else:
            os.environ["AGENTKIT_STATE_DIR"] = previous_state

    print(f"\n{'=' * 60}")
    print(f"agentkit tests: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
