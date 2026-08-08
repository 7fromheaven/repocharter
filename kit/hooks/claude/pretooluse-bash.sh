#!/usr/bin/env bash
# pretooluse-bash.sh — universal PreToolUse Bash safety gate.
# agentkit template. Derived from the ctx-fieldbook gate, whose regex discipline is the
# part worth keeping; the fail-open below it is the part that was wrong.
#
# Reads stdin JSON per the Claude Code hook spec:
#   { "tool_name": "Bash", "tool_input": { "command": "..." } }
# Emits a permissionDecision (ask|deny) on match, or a cwd-safety additionalContext on
# mutative git/fs ops. No match -> allow (silent, no output).
#
#   `ask`  = reversible-but-consequential  (rm -r, git reset --hard, push to protected)
#   `deny` = unambiguous foot-gun          (force-push to protected, checks-bypass flags)
#
# ── WHY THIS FAILS CLOSED ─────────────────────────────────────────────────────────────
# The ancestor of this script began with `command -v jq >/dev/null 2>&1 || exit 0`, on the
# reasoning that a missing tool should not brick the session. Under the hook spec that is
# a SILENT ALLOW of every Bash call: Claude Code treats exit 1 as a non-blocking error and
# proceeds, and "only exit code 2 blocks the action... If your hook is meant to enforce a
# policy, use `exit 2`." A policy gate that cannot read its input must refuse, loudly.
# So: try jq, fall back to python3, and exit 2 with an actionable message if neither is
# present. Both are near-universal; needing neither is not worth a silent hole.
#
# ── REGEX DISCIPLINE (do not weaken) ──────────────────────────────────────────────────
# 1. Anchor EVERY rule to COMMAND POSITION via CSEP: start-of-line (leading-whitespace
#    tolerant), OR after a shell separator ; && || |, OR inside $( ), optionally after
#    inline VAR=val assignments. grep segments on newlines, so per-line command-position
#    anchoring rules out HEREDOC bodies and commit-message text.
# 2. PORTABILITY: POSIX character classes ONLY. [[:space:]] not \s, [^[:alnum:]_] not \W.
#    \b \s \w are GNU-grep extensions that silently FAIL on BSD/macOS grep; POSIX word
#    boundaries ([[:<:]] [[:>:]]) are BSD-only. Neither is portable, so boundaries are
#    spelled out explicitly.
# 3. Chain greps with && when two tokens must co-occur in any order.
#
# ── CWD ASSUMPTION (rule 5) ───────────────────────────────────────────────────────────
# Rule 5 reports this hook process's own pwd as the Bash tool's persisted shell cwd. That
# holds when the harness spawns hooks in the tool shell's working directory. When
# CLAUDE_PROJECT_DIR is set and pwd falls outside it, the label softens to
# "cwd (unverified)" — treat that as advisory, not ground truth. The cd-target resolution
# NEVER executes command substitution sliced from the agent's command: only tilde and
# simple-variable forms are expanded, and anything containing backticks or $( ) is left
# unresolved and reported as a raw string.

set -euo pipefail

PAYLOAD=$(cat)

die_closed() {
  printf '%s\n' "agentkit safety gate: $1 The gate cannot evaluate this command, so it is refused rather than silently allowed. Install jq or python3, or remove the PreToolUse Bash hook deliberately." >&2
  exit 2
}

# ── Payload parsing + JSON emission, via whichever tool exists ────────────────────────
if command -v jq >/dev/null 2>&1; then
  CMD=$(printf '%s' "$PAYLOAD" | jq -r '.tool_input.command // ""' 2>/dev/null) ||
    die_closed "the hook payload could not be parsed as JSON."
  decide() {
    jq -nc --arg d "$1" --arg r "$2" \
      '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:$d,permissionDecisionReason:$r}}'
  }
  context() {
    jq -nc --arg c "$1" \
      '{hookSpecificOutput:{hookEventName:"PreToolUse",additionalContext:$c}}'
  }
elif command -v python3 >/dev/null 2>&1; then
  CMD=$(printf '%s' "$PAYLOAD" | python3 -c \
    'import json,sys;sys.stdout.write(str(json.load(sys.stdin).get("tool_input",{}).get("command","")))' 2>/dev/null) ||
    die_closed "the hook payload could not be parsed as JSON."
  decide() {
    python3 -c 'import json,sys;print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":sys.argv[1],"permissionDecisionReason":sys.argv[2]}}))' "$1" "$2"
  }
  context() {
    python3 -c 'import json,sys;print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":sys.argv[1]}}))' "$1"
  }
else
  die_closed "neither jq nor python3 is available."
fi

ask()   { decide ask   "$1"; exit 0; }
block() { decide deny  "$1"; exit 0; }

# Command-position anchor (+ optional inline env-var assignments). POSIX-portable.
CSEP='(^[[:space:]]*|[;&|][[:space:]]*|\$\([[:space:]]*)([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]+ +)*'

# Extra rm-safe paths a stack fragment may contribute (default: none).
SAFE_PATHS_EXTRA="${SAFE_PATHS_EXTRA:-}"

# ─── STACK-FRAGMENT INSERTION POINT ───────────────────────────────────────────────────
# `agentkit apply` splices the per-stack fragment (e.g. node-ts) HERE, before the
# universal rules, so it can add rules and extend SAFE_PATHS_EXTRA. The base is correct
# standalone; leave this marker in place.
# <<<AGENTKIT:STACK-FRAGMENT>>>
# ──────────────────────────────────────────────────────────────────────────────────────

# ─── REPO RULES, READ FROM THE DECLARATION ────────────────────────────────────────────
# Repo-specific gates come from .agents/compatibility.json `policy.denyBashPatterns` and
# `policy.askBashPatterns`. They are READ at runtime rather than spliced into this file,
# so there is one source of truth per repo and this script stays identical everywhere --
# which is what makes a fleet of repositories one system rather than N forks of a gate.
#
# Fails closed: a compatibility.json that exists but cannot be parsed refuses the command.
# A repo that declared its fences and then corrupted the file is exactly the case where
# waving the call through is wrong.
COMPAT="${CLAUDE_PROJECT_DIR:-.}/.agents/compatibility.json"
if [ -f "$COMPAT" ]; then
  # Emitted as: verb <TAB> reason <TAB> pattern [<TAB> pattern ...]
  # A rule may carry `pattern` (one) or `allOf` (several, ALL of which must match). The
  # conjunction form exists because gate scripts chain `grep A && grep B && block`, and
  # collapsing that to one pattern changes what the rule denies -- in one measured case it
  # turned a push-to-protected rule into one that denied `ls main.py`.
  if command -v jq >/dev/null 2>&1; then
    RULES=$(jq -r '
      ((.policy.denyBashPatterns // []) | map({v:"deny"} + .)) +
      ((.policy.askBashPatterns  // []) | map({v:"ask"}  + .))
      | .[]
      | . as $r
      | (if ($r.allOf // null) then $r.allOf else [$r.pattern] end) as $pats
      | select(($pats | length) > 0 and ($pats | map(select(. != null and . != "")) | length) == ($pats | length))
      | [$r.v, ($r.reason // "no reason given")] + $pats
      | join("\t")
    ' "$COMPAT" 2>/dev/null) || die_closed "policy in .agents/compatibility.json could not be parsed."
    # NOT @tsv: it escapes backslashes, so `\$\(` becomes `\\$\\(` and every regex carrying
    # an escape silently stops matching. join() passes the pattern through verbatim.
  else
    RULES=$(python3 - "$COMPAT" <<'PYEOF' 2>/dev/null
import json,sys
p=(json.load(open(sys.argv[1])).get("policy") or {})
for verb,key in (("deny","denyBashPatterns"),("ask","askBashPatterns")):
    for r in (p.get(key) or []):
        pats=r.get("allOf") or ([r["pattern"]] if r.get("pattern") else [])
        if pats and all(pats):
            print("\t".join([verb, r.get("reason","no reason given")] + pats))
PYEOF
    ) || die_closed "policy in .agents/compatibility.json could not be parsed."
  fi

  while IFS=$'\t' read -r RULE_VERB RULE_WHY RULE_PATS; do
    [ -z "${RULE_PATS:-}" ] && continue
    RULE_HIT=1
    OLD_IFS="$IFS"; IFS=$'\t'
    for RULE_PAT in $RULE_PATS; do
      [ -z "$RULE_PAT" ] && continue
      if ! echo "$CMD" | grep -qE "$RULE_PAT" 2>/dev/null; then
        RULE_HIT=0
        break
      fi
    done
    IFS="$OLD_IFS"
    if [ "$RULE_HIT" = "1" ]; then
      case "$RULE_VERB" in
        deny) block "Repo policy: ${RULE_WHY}" ;;
        # An `ask` is HELD rather than emitted, and the universal rules below get
        # their turn first. Measured 2026-08-09: one repo's broad WP-CLI ask-rule
        # matched `wp db drop` before the universal database block could run, and
        # because the gate exits on first match, a repo's own policy silently
        # downgraded a hard block to a prompt. A floor a repo can lower is not a
        # floor. A repo `deny` still fires immediately — that only strengthens.
        ask)  [ -z "${PENDING_ASK:-}" ] && PENDING_ASK="${RULE_WHY}" ;;
      esac
    fi
  done <<EOF
$RULES
EOF
fi
# ──────────────────────────────────────────────────────────────────────────────────────

# 1. Git push to a protected branch (block on --force same-line; else ask).
#
# The branch set comes from `policy.protectedBranches` in .agents/compatibility.json. It was
# previously hardcoded to (main|master) while the schema documented the field, so the
# declaration was dead config: a repo whose live deploy branch is `beast` could force-push it
# freely while believing it was protected. A declared setting that nothing reads is worse
# than no setting, because it reads as configured.
PROTECTED="${AGENTKIT_PROTECTED_BRANCHES:-}"
if [ -z "$PROTECTED" ] && [ -f "$COMPAT" ]; then
  if command -v jq >/dev/null 2>&1; then
    PROTECTED=$(jq -r '(.policy.protectedBranches // ["main","master"]) | join("|")' "$COMPAT" 2>/dev/null || true)
  elif command -v python3 >/dev/null 2>&1; then
    PROTECTED=$(python3 -c 'import json,sys;p=(json.load(open(sys.argv[1])).get("policy") or {});print("|".join(p.get("protectedBranches") or ["main","master"]))' "$COMPAT" 2>/dev/null || true)
  fi
fi
PROTECTED="(${PROTECTED:-main|master})"
PUSH_LINES=$(echo "$CMD" | grep -E "${CSEP}git +push([^[:alnum:]_]|$)" | grep -E "(^|[^[:alnum:]_])${PROTECTED}([^[:alnum:]_]|$)" || true)
if [[ -n $PUSH_LINES ]]; then
  if echo "$PUSH_LINES" | grep -qE -- '--force([^[:alnum:]_]|$)|(^|[[:space:]])-f([^[:alnum:]_]|$)'; then
    block "Safety gate: git push --force to a protected branch ${PROTECTED} is BLOCKED — force-push overwrites shared history. Use a feature branch and a PR."
  fi
  ask "Safety gate: git push to a protected branch ${PROTECTED}. Confirm with the operator before pushing to shared history."
fi

# 2. Checks-bypass attempts (block). The bypass flag must co-occur with a real git write
#    verb on the SAME line, so a --no-verify quoted inside a commit-message body is exempt.
if echo "$CMD" | grep -qE "${CSEP}git +(.*[^[:alnum:]_])?(commit|push|rebase|merge|cherry-pick|am|tag)(.*[[:space:]])(--no-verify|--no-gpg-sign)([^[:alnum:]_]|$)" ||
  echo "$CMD" | grep -qE "${CSEP}git +.*commit\.gpgsign=false"; then
  block "Safety gate: checks-bypass (--no-verify / --no-gpg-sign / commit.gpgsign=false) is BLOCKED. Investigate the failing gate; do not route around it."
fi

# 2b. Bypassing the hooks path itself (block). `git -c core.hooksPath=/dev/null push`
#     defeats every git-hook control at once, and the fieldbook's --no-verify regex never
#     saw it. Named here because git hooks are a Tier-3 control the agent can rewrite:
#     this rule makes the bypass loud, not impossible.
if echo "$CMD" | grep -qE "${CSEP}git +.*-c +core\.hooksPath="; then
  block "Safety gate: overriding core.hooksPath bypasses every git hook in one flag. BLOCKED. If a hook is wrong, fix the hook."
fi

# 3. git reset --hard (ask) — destroys uncommitted working-tree changes.
echo "$CMD" | grep -qE "${CSEP}git +reset +--hard([^[:alnum:]_]|$)" &&
  ask "Safety gate: 'git reset --hard' destroys uncommitted changes. Confirm cwd and target with the operator."

# 3b. Working-tree-revert cousins of reset --hard (ask). Each destroys uncommitted work
#     with no recovery path. Field incident: a verifier's `git checkout -- <file>` on an
#     UNCOMMITTED change-under-review reverted to HEAD and discarded the fix. checkout is
#     exempt ONLY for -b/-B (new-branch creation, always safe).
if echo "$CMD" | grep -qE "${CSEP}git +checkout([^[:alnum:]_]|$)" &&
  ! echo "$CMD" | grep -qE "${CSEP}git +checkout +-[bB]([^[:alnum:]_]|$)"; then
  ask "Safety gate: 'git checkout' (other than -b/-B) can irrecoverably revert uncommitted changes. Confirm the target — and to un-apply a change under review, reverse the edit in place; never discard the working tree."
fi
echo "$CMD" | grep -qE "${CSEP}git +restore([^[:alnum:]_]|$)" &&
  ask "Safety gate: 'git restore' reverts uncommitted changes irrecoverably. Confirm the target with the operator."
# Force may arrive as an adjacent cluster (-fd), a SEPARATED cluster (-d -f), or the long
# form (--force), anywhere within the clean invocation — but never across a command
# separator (;&|), so a force flag on a LATER command cannot false-fire. The dash must
# follow whitespace (a real flag token), so a filename containing "-f" stays silent.
# The f/F match is ANYWHERE in the cluster: `git clean -fd` must not slip because f is not
# the last character. That was a real first-ship bug, caught only by testing the gate.
echo "$CMD" | grep -qE "${CSEP}git +clean( +[^;&|]*)? +(-[A-Za-z]*[fF][A-Za-z]*|--force)([^[:alnum:]_]|$)" &&
  ask "Safety gate: 'git clean' with a force flag (any position: -f/-fd/-d -f/--force) deletes untracked files irrecoverably. Confirm the target."
echo "$CMD" | grep -qE "${CSEP}git +stash +(drop|clear)([^[:alnum:]_]|$)" &&
  ask "Safety gate: 'git stash drop/clear' discards stashed work irrecoverably. Confirm with the operator."

# 4. Recursive rm outside safe paths (ask). Matches any short-flag cluster containing r/R
#    (-rf, -fr, -Rf, -rfv, -r) or --recursive.
SAFE_PATHS="/tmp/|/private/tmp/|\\.cache|/dist/|/build/|/gen/|coverage${SAFE_PATHS_EXTRA:+|${SAFE_PATHS_EXTRA}}"
if echo "$CMD" | grep -qE "${CSEP}rm +(-[A-Za-z]*[rR][A-Za-z]*|--recursive)([^[:alnum:]_]|$)" &&
  ! echo "$CMD" | grep -qE "$SAFE_PATHS"; then
  ask "Safety gate: recursive rm outside safe paths (${SAFE_PATHS}). Confirm the target with the operator."
fi

# 5. Destructive database operations.
#    Requires BOTH a database tool AND a destructive verb, deliberately. A rule that
#    fired on the words alone would block `grep -rn "DROP TABLE" .` — auditing FOR the
#    danger must stay possible, which is the same trap that once made a harvested rule
#    deny `ls main.py`. Underscores do not count as the separator, so a filename like
#    `001_drop_table.sql` reads as a name and not as a verb.
#
#    Split by severity for one reason: a gate that blocks routine work gets switched
#    off, and then it protects nothing. Wiping a database is never routine, so it is
#    blocked. Resetting one is an everyday local-dev command, so it asks.
#
#    KNOWN LIMIT, stated rather than implied: this reads the COMMAND. Destructive SQL
#    inside a file (`psql < migration.sql`) is invisible to it.
DB_TOOL='psql|mysql|mariadb|sqlite3|mongo|mongosh|redis-cli|prisma|supabase|sequelize|knex|alembic|flyway|liquibase|drizzle-kit|turso|wrangler +d1|rails|artisan|manage\.py|sanity|wp'
DB_WIPE='drop +(table|database|schema|index|view)|db +(drop|clean)|truncate +table|truncate +[a-z_]+|flushall|flushdb|dropdatabase|--accept-data-loss|dataset +delete'
DB_RESET='(migrate|db) +reset|db:(drop|reset)|migrate:(reset|fresh)|db +push +--force'

# Database tools are almost never invoked bare: `npx prisma`, `php artisan`,
# `bundle exec rails`, `python3 manage.py`. Anchoring at command position alone
# missed four of eight real destructive commands on the first test run, so an
# optional runner is allowed in front — while still requiring command position,
# which is what keeps `grep -rn "DROP TABLE" .` out of the match.
DB_RUNNER='npx +(-y +)?|bunx +|pnpm +(exec +|dlx +)|yarn +(exec +|dlx +)?|npm +exec +|php +|python3? +|bundle +exec +|poetry +run +|docker +(compose +)?(exec|run) +[^;&|]*'

if echo "$CMD" | grep -qiE "${CSEP}(${DB_RUNNER})?(${DB_TOOL})([^[:alnum:]_]|$)"; then
  echo "$CMD" | grep -qiE "(^|[^[:alnum:]_])(${DB_WIPE})" &&
    block "Safety gate: destructive database operation is BLOCKED — dropping, truncating or flushing destroys data that no branch protection or backup policy here covers. If this is genuinely intended, the operator runs it."
  echo "$CMD" | grep -qiE "(^|[^[:alnum:]_])(${DB_RESET})" &&
    ask "Safety gate: this resets a database, which discards its current contents. Routine against a local dev database and catastrophic against a shared one — confirm which you are pointed at."
fi

# Tools whose whole purpose is destruction take no verb: the command IS the damage.
echo "$CMD" | grep -qE "${CSEP}(dropdb|dropuser)([^[:alnum:]_]|$)" &&
  block "Safety gate: '${CMD%% *}' destroys a database or role outright. The operator runs this, not an agent."

# Every universal BLOCK has now had its turn. A repo `ask` held above is safe to emit.
[ -n "${PENDING_ASK:-}" ] && ask "Repo policy: ${PENDING_ASK}"

# 6. CWD-awareness injection on mutative git / filesystem ops (context-inject, no block).
#    The harness preserves cwd between Bash calls, so a prior `cd <subdir>` for a read-only
#    op can silently set the wrong context for a later mutative op. Ask-gated commands
#    above have already exited.
MUTATIVE_PATTERN='git +(add|checkout|clean|commit|rm|mv|reset|merge|rebase|cherry-pick|stash|worktree|revert|restore|push)([^[:alnum:]_]|$)|rm +|mv +'
if echo "$CMD" | grep -qE "${CSEP}(${MUTATIVE_PATTERN})"; then
  set +e
  PWD_NOW=$(pwd 2>/dev/null || echo "<pwd-failed>")
  CWD_PREFIX="cwd"
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then
    case "$PWD_NOW/" in
      "${CLAUDE_PROJECT_DIR%/}/"*) ;;
      *) CWD_PREFIX="cwd (unverified)" ;;
    esac
  fi
  INTENDED_CWD=""
  CWD_NOTE=""
  if echo "$CMD" | grep -qE '^[[:space:]]*cd[[:space:]]+'; then
    RAW_TARGET=$(echo "$CMD" | sed -nE 's|^[[:space:]]*cd[[:space:]]+([^&|;]+).*|\1|p' | sed 's/[[:space:]]*$//' | head -1)
    case "$RAW_TARGET" in
      \"*\") RAW_TARGET="${RAW_TARGET#\"}"; RAW_TARGET="${RAW_TARGET%\"}" ;;
      \'*\') RAW_TARGET="${RAW_TARGET#\'}"; RAW_TARGET="${RAW_TARGET%\'}" ;;
    esac
    # SAFE expansion only — nothing sliced from the agent's command may ever execute here.
    case "$RAW_TARGET" in
      '') ;;
      *\`*|*\$\(*)
        CWD_NOTE=" | cd target contains command substitution — NOT resolved; raw: ${RAW_TARGET}" ;;
      '~'|'~/'*)
        INTENDED_CWD="${HOME}${RAW_TARGET#\~}" ;;
      *\$*)
        VAR_NAME=$(printf '%s' "$RAW_TARGET" | sed -nE 's|^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?(/.*)?$|\1|p')
        VAR_TAIL=$(printf '%s' "$RAW_TARGET" | sed -nE 's|^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?(/.*)?$|\2|p')
        if [ -n "$VAR_NAME" ] && [ -n "${!VAR_NAME:-}" ]; then
          INTENDED_CWD="${!VAR_NAME}${VAR_TAIL}"
        else
          CWD_NOTE=" | cd target uses a variable this hook cannot resolve; raw: ${RAW_TARGET}"
        fi ;;
      *)
        INTENDED_CWD="$RAW_TARGET" ;;
    esac
    if [ -n "$INTENDED_CWD" ] && [ "${INTENDED_CWD#/}" = "$INTENDED_CWD" ]; then
      INTENDED_CWD="$PWD_NOW/$INTENDED_CWD"
    fi
  fi
  if [ -n "$INTENDED_CWD" ] && [ -d "$INTENDED_CWD" ]; then
    EFFECTIVE_CWD="$INTENDED_CWD"
    CWD_LABEL="${CWD_PREFIX}=${INTENDED_CWD} (via cd-chain from shell-cwd=${PWD_NOW})"
  else
    EFFECTIVE_CWD="$PWD_NOW"
    CWD_LABEL="${CWD_PREFIX}=${PWD_NOW}"
  fi
  CWD_LABEL="${CWD_LABEL}${CWD_NOTE}"
  BRANCH=$(git -C "$EFFECTIVE_CWD" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "<not-a-git-repo>")
  REPO_ROOT=$(git -C "$EFFECTIVE_CWD" rev-parse --show-toplevel 2>/dev/null || echo "<not-a-git-repo>")
  STATUS_COUNT=$(git -C "$EFFECTIVE_CWD" status -s 2>/dev/null | wc -l | tr -d ' ' || echo "?")
  STATUS_TOP=$(git -C "$EFFECTIVE_CWD" status -s 2>/dev/null | head -3 | tr '\n' ';' | sed 's/;$//' || true)
  context "[cwd-safety] ${CWD_LABEL} | branch=${BRANCH} | repo-root=${REPO_ROOT} | changes=${STATUS_COUNT} | top: ${STATUS_TOP}"
  set -e
  exit 0
fi

# No match -> allow (no output = no action, per the hook spec).
exit 0
