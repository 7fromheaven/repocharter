# RepoCharter mechanics

The mechanical layer of the portable agent-context system. One vendored copy per
repository, one version stamp, one command to install and one to check.

`kit/repocharter` is the canonical executable. `kit/agentkit` remains a silent compatibility
wrapper so existing repositories and automation continue to work.

**Status: in production across a fleet of repositories.** 389 tests, no dependencies:
`python3 kit/tests/run_tests.py`.

For most repositories, the complete path is `census` → `apply` → `self-test` / `measure` →
`verify`. The migration commands are specialized compatibility tools retained for recognized
legacy layouts; they are not required for normal adoption.

For an agent-owned adoption, upgrade, provider promotion, or integration, use
`kit/skills/migrate-repocharter/SKILL.md`. The public distribution exposes that one bundled body as
`$migrate-repocharter` in Codex and `/migrate-repocharter` in Claude Code before target installation;
name the target repository in the invocation. `apply` installs the same workflow through the
existing project-skills adapter. Its checkpoint distinguishes current evidence from stale evidence,
so a branch switch or documentation-only commit does not rerun the full provider matrix. If Codex
silently substitutes the primary checkout's hooks for a linked worktree, the workflow preserves the
current state in an independent normal clone instead of bypassing hook trust.

## Install into a repository

See **The order for one repository** below. `apply` is idempotent — re-running it on a
conforming repo reports `0 change(s)`.

## What apply writes, and what it refuses to write

**Writes** (all mechanical, all regenerable):

- `.agents/compatibility.json` — the declaration, merged rather than overwritten
- `.claude/hooks/agentkit/*` — the gate scripts, vendored not symlinked
- `.claude/settings.json` — the managed `hooks`, `permissions.deny`, and declared auto-memory
  state; every other key, including your allow-list, is preserved untouched
- `.codex/hooks.json` — current project-hook schema, merged with foreign hook groups and keys
- `.claude/skills/<name>` — relative symlinks into `.agents/skills/`
- `.agnix.toml` — linter config with severities raised on the six rules that matter
- `CLAUDE.md` — a relative symlink to `AGENTS.md`, **only if absent**

**Never writes:** `AGENTS.md`'s body or anything under `docs/project/`. Those files contain
project-specific knowledge and remain human-authored. Copy `kit/templates/AGENTS.md.tmpl`
and fill it in yourself.

`migrate-repocharter` is the one product-managed project skill and is refreshed on upgrade;
otherwise a fixed workflow could remain stale in the exact repositories that need it. Existing
project skills, including any customized `project-memory`, are preserved rather than overwritten.

Claude auto memory defaults **off** because `docs/project/` is the reviewed, portable home for
durable knowledge. An explicit `autoMemory: "on"` exception must carry `autoMemoryReason`; `apply`
then writes the matching `autoMemoryEnabled` setting. When upgrading an older RepoCharter manifest,
an unreasoned inherited `on` is migrated to `off`. On a first installation only, an explicit
pre-existing `autoMemoryEnabled: true` is preserved and surfaced with a reason for review.

## The commands

| Use | Command | Does | Exit |
|---|---|---|---|
| Core | `census` | measures startup context and existing configuration before anything changes | 0 |
| Core | `apply` | installs the mechanical layer, idempotently (`--dry-run`) | 0 / 2 |
| Core | `self-test` | fires forbidden and benign calls; provider promotion also proves target trust/settings and writes checkout-local evidence | 0 / 1 |
| Core | `measure` | fires repository-declared policy and reports enforced, broken, or unmeasured rules | 0 / 1 |
| Core | `verify` | runs checked-in schema, adapter, budget, residue, and validation checks; `--agnix` is optional | 0 / 1 |
| Team | `fleet` | runs stage + census for every repo under a root, in one table | 0 |
| Specialized | `policy scaffold` | harvests a draft policy from a supported hand-written shell gate | 0 / 2 |
| Specialized | `policy promote` | moves the reviewed draft into the live policy | 0 / 1 |
| Specialized | `supersede` | proves the new gate is never weaker, then retires the supported legacy gate | 0 / 1 |
| Specialized | `migrate` | handles recognized legacy residue and writes a plan for judgment calls | 0 / 2 |
| Specialized | `revert` | undoes the last mechanical migration from its manifest | 0 / 1 |

### The order for one repository

An agent should execute this sequence through `$migrate-repocharter`; the commands are shown here so
the protocol stays reviewable.

```sh
kit/repocharter census  --repo ~/dev/foo             # measure before touching
kit/repocharter apply   --repo ~/dev/foo --dry-run   # preview the footprint
kit/repocharter apply   --repo ~/dev/foo             # refuses a dirty tree
kit/repocharter self-test --repo ~/dev/foo           # prove the gates fire
kit/repocharter measure --repo ~/dev/foo              # test repo-declared policy
kit/repocharter verify  --repo ~/dev/foo              # check the installed system
# Under Codex: restart, review/trust /hooks, then:
kit/repocharter self-test --repo ~/dev/foo --promote-codex
kit/repocharter verify --repo ~/dev/foo --effective
# Under Claude Code: restart, establish interactive workspace trust, then:
kit/repocharter self-test --repo ~/dev/foo --promote-claude
```

### Only when replacing a supported legacy system

The current compatibility tools recognize a specific legacy directory and retired-skill layout.
Policy harvesting recognizes hand-written `.claude/hooks/*.sh` gates that express decisions using
the legacy `block` / `ask` functions with nearby `grep -qE` conditions. Other context systems,
memory stores, and validator shapes require manual review.

```sh
kit/repocharter policy scaffold --repo ~/dev/foo
#   ... review policyDraft ...
kit/repocharter policy promote  --repo ~/dev/foo
kit/repocharter apply   --repo ~/dev/foo             # recompile permissions.deny
kit/repocharter supersede --repo ~/dev/foo           # can the old gate be retired?
kit/repocharter supersede --repo ~/dev/foo --retire  # only if the check says yes
kit/repocharter migrate --repo ~/dev/foo             # plan; --apply does the mechanical half
```

## Replacing a supported legacy shell gate

When a repository already has a supported hand-written Bash gate, `apply` only ever **adds** — that
is what stops it disarming the repo mid-install. Immediately after `apply`, both the original gate
and RepoCharter's gate remain wired to `PreToolUse`/`Bash` and both fire.

`supersede` is what ends that. It runs **both** gates over a corpus of commands and
compares verdicts, then retires the old one only if all three hold:

1. **Never weaker.** The kit's verdict is at least as strict on every probe.
2. **Never over-broad.** The kit does not gate any obviously-benign command.
3. **Nothing dropped.** No rule sits unharvested in `policyDraft.needsManualPattern`.

Retirement quarantines the script and unwires it from `settings.json`; `revert` puts it
back. The probe corpus is mined from the **old gate's own patterns**, so every rule it
encodes is exercised by a command derived from that rule — plus a fixed dangerous set. It
is a sample, not a proof, and the report says so.

### Why benign probes are required

"Never weaker" alone is insufficient because a rule that denies everything satisfies it.
`policy scaffold` therefore tests candidates against a benign corpus and refuses to promote
over-broad patterns. Gate rules also support `allOf`, preserving both halves of a chained
`grep A && grep B` condition.

`apply` **refuses a dirty working tree** unless you pass `--allow-dirty`, so an install
stays reviewable on its own rather than mixed into someone's in-progress work. Its own
footprint is excluded from that check, so it can always re-run over itself.

## Policy verification semantics

**Empty repository policy.** The universal floor remains active when `policy` is empty: it
covers force-push, checks bypass, destructive database operations, recursive removal outside
safe paths, and history-losing Git commands. Repository-specific policies are needed only for
boundaries a general catalogue cannot know, such as production deploys, credential surfaces,
generated files, and live-data operations. `fleet` reports `POLICY-EMPTY` for visibility but
`verify` does not treat it as an error by itself.

**Inert rules.** A pattern that silently matches nothing looks exactly like one that works.
`policy scaffold` resolves the source script's shell variables, translates GNU escapes to
POSIX, and validates every pattern with `grep -E` itself — anything it cannot make portable
goes to `needsManualPattern` instead of shipping. `verify` independently lints the **live**
policy and errors on any inert rule, however it got there.

## Migration stages

`verify` derives a stage from the filesystem and splits findings accordingly:

| Stage | Meaning |
|---|---|
| `not-started` | no RepoCharter layer — run `apply` |
| `mechanical` | plumbing installed, recognized legacy cleanup incomplete — run `migrate` if converting that layout |
| `migrated` | canonical layout, no recognized legacy residue |

**BLOCKING** findings (dead references, override files, absolute symlinks, budget overruns,
inert policy rules) are errors at every stage. **MIGRATION** findings (recognized legacy residue,
retired skills, missing `docs/project/`) are `TODO` while a repo is mid-migration and become
errors once it claims to be done. This keeps incomplete migration work distinct from defects
in repositories that already claim the canonical layout.

Stage is deliberately structural. Unscoped rules and nested `AGENTS.md` are *not* stage
markers, because a finished repo can acquire either by committing one bad file — and if
they counted, that commit would silently demote the repo and downgrade its own error.

`verify --effective` additionally diffs Codex's model-visible prompt against the declared
ledger. Its result depends on machine-local workspace trust, so an untrusted CI checkout
cannot observe trusted-workspace configuration. Use the static verifier in CI and
`--effective` on a trusted developer checkout; they cover different surfaces.

`self-test` is what entitles a repo to report **verified enforcement**. The compatibility manifest
currently serializes that state as `enforcement: blocking`; this is a technical enum, not a claim
that RepoCharter or the repository is blocked. For Codex, promotion records the exact adapter hash
and installed CLI version only after Bash, `apply_patch`, and MCP are denied through the real CLI
and an ordinary no-bypass run proves that project trust persists. Open `/hooks` after installation
or any adapter change; a temporary trust override proves code paths, not operational enforcement.

Before Codex promotion, RepoCharter queries `hooks/list` for the exact checkout and requires every
project hook source to resolve to that checkout's `.codex/hooks.json`. Zero hooks or a source path
from another checkout fails before live probes. Codex 0.147 currently resolves linked-worktree
project hooks from the primary checkout; after one exact persisted-trust retry reproduces that
result, continue from an independent clone with a normal `.git/` directory.

When `apply` preserves a foreign Codex hook, promotion derives a conservative closure from its
command. Static checkout-relative scripts, including the exact
`$(git rev-parse --show-toplevel)/...` form, are copied into the disposable probe and hashed into
both attestations. Missing files, external implementations, variables, additional command
substitutions, globs, and shell control flow fail clearly. The foreign group remains installed;
never delete or bypass it to obtain a passing promotion.

For Claude Code, `--promote-claude` drives the real `claude` executable in a disposable repository
with project and local settings loaded — never `--bare`, which skips hooks and would certify a wire
path the probe never used. The intended tool call and matching `hook_response` must appear in
structured event output. Every probe tool is pre-allowed and the probe repo's `permissions.deny`
is emptied, so a native permission refusal cannot be mistaken for the gate. Bash, Write and MCP are
each proved in both directions, and the allowed write must also show its post-write response and
measurement. Promotion separately requires persisted trust covering the target and runs a second
probe with that checkout's effective user, project, and local settings.

ConfigChange is not scored as a live event. Default permission mode may refuse writes before the
hook, but bypass-authorized writes are possible; the measured limitation is that headless `-p`
sessions emitted no watcher event after authorized Write, Bash, or external settings mutations. The
guard is fired directly in a throwaway fixture against the provider-shaped source/path payload and
the exact five handler mappings. Effective settings are inspected separately for disarm switches.

`verify` re-derives each recorded adapter hash and re-reads the installed provider version. When a
provider CLI is present, it also requires an exact checkout-local record under the worktree's
absolute Git directory. A clone, sibling worktree, edited gate, upgraded CLI, or changed RepoCharter
version therefore cannot inherit yesterday's machine claim. Provider-neutral CI without that CLI
still performs portable byte checks and reports the unavailable provider check as a note.

An explicit promotion request reuses current checkout evidence when all of those bindings still
match, Claude workspace trust/effective settings remain valid, and Codex reports the exact trusted
hook source. Otherwise it runs the live provider probes and fails closed. This makes integration of
an unchanged migration commit cheap without making tracked `blocking` text self-certifying.

## The gates

| Policy surface | Claude Code | Codex | Enforces |
|---|---|---|---|
| Shell | `Bash` | `Bash` | force-push, checks-bypass, `core.hooksPath` override, destructive git, recursive rm, repo policy |
| Writes | `Edit\|Write\|NotebookEdit` | `apply_patch` | `denyWritePaths`; Codex parses every path in a multi-file patch |
| MCP | `mcp__.*` | `mcp__.*` | `denyMcpTools`, narrowable by argument |
| Post-write | `Edit\|Write` | `apply_patch` | takes the after-measurement and reports the delta |
| Config | `ConfigChange` | write guard on `.codex/hooks.json` and `.codex/config.toml` | refuses a settings reload that sets `disableAllHooks`, changes any exact kit handler mapping, or empties a declared `permissions.deny` |

Codex does not support an interactive `ask` decision in `PreToolUse`. In an approval-capable turn,
the adapter labels the call as confirmation-required and hands it to Codex's native permission
flow; with approvals disabled, it denies the call. Because `PreToolUse` cannot itself open the
prompt, the agent must submit confirmation-class calls for native approval. Unconditional `deny`
rules remain blocking in either mode.

**Everything fails closed.** A gate that cannot parse its input, read its policy, or compile
a pattern exits 2. Claude Code treats exit 1 as non-blocking and proceeds; exit 2 blocks.
The missing-parser path is covered directly by the test suite.

## Policy lives in one file

Everything the gates enforce is declared in `.agents/compatibility.json` under `policy`.
There is no second config file, and the hooks are not edited per repo.

```jsonc
"policy": {
  "denyWritePaths": [
    { "glob": "lib/content/live/**", "reason": "generated from the CMS; hand-edits are lost on the next sync." }
  ],
  "denyMcpTools": [
    { "pattern": "^mcp__.*__deploy_to_vercel$", "reason": "cutover is an operator-run event.",
      "whenArgMatches": { "target": "^production$" } }
  ],
  "measureOnWrite": [
    { "glob": "content/localized/**", "measure": "script-lengths", "scripts": ["Cyrillic"],
      "reason": "report a quantitative change for localized content." }
  ]
}
```

`denyWritePaths` is compiled into Claude's `Edit(...)` permission matcher **and** checked by
the hook, deliberately twice. Claude's `Edit` matcher covers every file-editing tool; a
`Write(...)` path matcher is unsupported. The permission system is the client-enforced layer
Anthropic points at for hard path blocks, and the hook is what still fires for explicit
Edit/Write events if a settings layer is missing or overridden.

### `measureOnWrite`

`measureOnWrite` records a configured metric before and after each matching edit and reports
the delta. Supported measurements include Unicode-script character counts, byte size, and
line count. The hook performs the measurement directly instead of relying on a prose reminder.

## Testing

```sh
python3 kit/tests/run_tests.py
```

389 tests, no dependencies. Most of them are negative — what each gate must **refuse** —
because a gate that allows everything passes any happy-path suite.

## CI

Copy `kit/templates/ci-workflow.yml` to `.github/workflows/repocharter.yml`. The workflow blocks
merges only when branch protection or a repository ruleset requires the job to pass; otherwise
it reports violations after a push.

## What this deliberately does not do

- **No instruction duplication layer.** RepoCharter keeps `AGENTS.md` canonical and uses
  declared, verifier-checked provider adapters.
- **No re-implementation of agnix.** The shared catalogue remains opt-in through
  `verify --agnix`; default verification is checked in, dependency-free, and offline.
- **No submodule.** Each repository receives a versioned vendored runtime so fresh clones and
  CI do not depend on submodule initialization.
