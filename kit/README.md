# agentkit

The mechanical layer of the portable agent-context system. One vendored copy per
repository, one version stamp, one command to install and one to check.

**Status: in production across a fleet of repositories.** 176 tests, no dependencies:
`python3 kit/tests/run_tests.py`. Every rule below was found by a real migration, and the
commentary cites the failure that produced it.

## Install into a repository

See **The order for one repository** below. `apply` is idempotent — re-running it on a
conforming repo reports `0 change(s)`.

## What apply writes, and what it refuses to write

**Writes** (all mechanical, all regenerable):

- `.agents/compatibility.json` — the declaration, merged rather than overwritten
- `.claude/hooks/agentkit/*` — the gate scripts, vendored not symlinked
- `.claude/settings.json` — **only** the `hooks` key and `permissions.deny`; every other
  key, including your allow-list, is preserved untouched
- `.claude/skills/<name>` — relative symlinks into `.agents/skills/`
- `.agnix.toml` — linter config with severities raised on the six rules that matter
- `CLAUDE.md` — the `@AGENTS.md` shim, **only if absent**

**Never writes:** `AGENTS.md`'s body, anything under `docs/project/`. A tool that generates
truth recreates the accretion problem in a new costume. Copy
`kit/templates/AGENTS.md.tmpl` and fill it in yourself.

## The commands

| Command | Does | Exit |
|---|---|---|
| `fleet` | stage + census for every repo under a root, one table | 0 |
| `census` | the Phase-0 measurement, before anything is changed | 0 |
| `apply` | installs the mechanical layer, idempotently (`--dry-run`) | 0 / 2 |
| `policy scaffold` | harvests a draft policy from the repo's **own** rules | 0 / 2 |
| `policy promote` | moves the reviewed draft into the live policy | 0 / 1 |
| `supersede` | proves the kit's gate is never weaker, then retires the legacy one | 0 / 1 |
| `migrate` | mechanical migration + a written plan for the judgment calls | 0 / 2 |
| `verify` | agnix (445 shared rules) + the residue checks, stage-aware | 0 / 1 |
| `self-test` | fires forbidden calls at the installed hooks and asserts refusal | 0 / 1 |
| `revert` | undoes the last mechanical migration from its manifest | 0 / 1 |

### The order for one repository

```sh
kit/agentkit census  --repo ~/dev/foo             # measure before touching
kit/agentkit apply   --repo ~/dev/foo --dry-run   # preview the footprint
kit/agentkit apply   --repo ~/dev/foo             # refuses a dirty tree
kit/agentkit policy scaffold --repo ~/dev/foo     # harvest its real rules
#   ... review policyDraft ...
kit/agentkit policy promote  --repo ~/dev/foo
kit/agentkit apply   --repo ~/dev/foo             # recompile permissions.deny
kit/agentkit self-test --repo ~/dev/foo           # prove the gates fire
kit/agentkit supersede --repo ~/dev/foo           # can the old gate be retired?
kit/agentkit supersede --repo ~/dev/foo --retire  # only if the check says yes
kit/agentkit migrate --repo ~/dev/foo             # plan; --apply does the mechanical half
```

## Replacing the old system, not sitting beside it

`apply` only ever **adds** — that is what stops it disarming a repo mid-install. The
consequence is that immediately after `apply` you have two systems live: the repo's
original gate and the kit's, both wired to `PreToolUse`/`Bash`, both firing.

`supersede` is what ends that. It runs **both** gates over a corpus of commands and
compares verdicts, then retires the old one only if all three hold:

1. **Never weaker.** The kit's verdict is at least as strict on every probe.
2. **Never over-broad.** The kit does not gate any obviously-benign command.
3. **Nothing dropped.** No rule sits unharvested in `policyDraft.needsManualPattern`.

Retirement quarantines the script and unwires it from `settings.json`; `revert` puts it
back. The probe corpus is mined from the **old gate's own patterns**, so every rule it
encodes is exercised by a command derived from that rule — plus a fixed dangerous set. It
is a sample, not a proof, and the report says so.

### Why check 2 exists

"Never weaker" alone is not a bar. A rule that denies everything is trivially never weaker,
passes the comparison, and then blocks honest work until somebody switches the gate off —
which is the failure this entire system is built to avoid.

It is not hypothetical. Harvesting one repository's push-to-protected rule picked up its
bare `main|master` branch matcher without the `git push` half, producing a policy that
**denied `ls main.py`**. Two defenses now: `policy scaffold` tests every candidate against
a benign corpus and refuses to promote an over-broad one, and gate rules support `allOf`
so a chained `grep A && grep B` keeps both halves instead of being flattened to one.

`apply` **refuses a dirty working tree** unless you pass `--allow-dirty`, so an install
stays reviewable on its own rather than mixed into someone's in-progress work. Its own
footprint is excluded from that check, so it can always re-run over itself.

## Two failure modes this is built around

**Installed but inert.** `apply` on its own gives you five gates with an empty policy, and
`self-test` passes because it only exercises the built-in universal rules. That reads as
green while protecting nothing repo-specific — the most dangerous state in the system,
because nobody looks twice at a checkmark. `verify` now raises a **warning** for an empty
policy, `apply` says so on the way out, and `fleet` prints `POLICY-EMPTY` in the table.

**Inert rules.** A pattern that silently matches nothing looks exactly like one that works.
`policy scaffold` resolves the source script's shell variables, translates GNU escapes to
POSIX, and validates every pattern with `grep -E` itself — anything it cannot make portable
goes to `needsManualPattern` instead of shipping. `verify` independently lints the **live**
policy and errors on any inert rule, however it got there.

## Stage, so ten repos don't look like ten failures

`verify` derives a stage from the filesystem and splits findings accordingly:

| Stage | Meaning |
|---|---|
| `not-started` | no agentkit layer — run `apply` |
| `mechanical` | plumbing installed, migration incomplete — run `migrate` |
| `migrated` | canonical layout, no fieldbook residue |

**BLOCKING** findings (dead references, override files, absolute symlinks, budget overruns,
inert policy rules) are errors at every stage. **MIGRATION** findings (fieldbook residue,
retired skills, missing `docs/project/`) are `TODO` while a repo is mid-migration and become
errors once it claims to be done. Without the split, ten un-migrated repos produce ninety
identical-looking errors and there is no signal at all.

Stage is deliberately structural. Unscoped rules and nested `AGENTS.md` are *not* stage
markers, because a finished repo can acquire either by committing one bad file — and if
they counted, that commit would silently demote the repo and downgrade its own error.

`verify --effective` additionally diffs Codex's real model-visible prompt against the
declared ledger. **It has a measured false negative:** `codex debug prompt-input` reports
different content depending on machine-local workspace trust, so an untrusted checkout —
which is what a CI runner is — cannot see a `.codex/config.toml` amputation. The static
check is the CI gate; `--effective` is the developer-machine check. They are not
interchangeable.

`self-test` is what entitles a repo to declare `enforcement: blocking` in
`compatibility.json`. Every control below Tier 0 fails open, so an untested gate is a
guess, not a fence.

## The gates

| Matcher | Hook | Enforces |
|---|---|---|
| `Bash` | `pretooluse-bash.sh` | force-push, checks-bypass, `core.hooksPath` override, destructive git, recursive rm, cwd-safety context |
| `Edit\|Write\|NotebookEdit` | `pretooluse-write.py` | `denyWritePaths`, and takes the before-measurement |
| `mcp__.*` | `pretooluse-mcp.py` | `denyMcpTools`, narrowable by argument |
| `Edit\|Write` (Post) | `posttooluse-write.py` | takes the after-measurement and reports the delta |
| `ConfigChange` | `configchange-guard.py` | refuses `disableAllHooks`, emptying `permissions.deny`, dropping a hook event |

**Everything fails closed.** A gate that cannot parse its input, read its policy, or
compile a pattern exits 2. Claude Code treats exit 1 as non-blocking and proceeds; only
exit 2 blocks. The ancestor of the Bash gate exited **0** when `jq` was missing, which was
a silent allow of every command, and nothing noticed. That specific regression is covered
by a test.

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
    { "glob": "lib/content/**", "measure": "script-lengths", "scripts": ["Cyrillic"],
      "reason": "the operator cannot read Cyrillic and needs a number." }
  ]
}
```

`denyWritePaths` is compiled into `permissions.deny` **and** checked by the hook,
deliberately twice: the permission system is the client-enforced layer Anthropic points at
for hard path blocks, and the hook is what still fires if a settings layer is missing or
overridden.

### `measureOnWrite` is the important one

It exists for rules like *"report Cyrillic string lengths before and after any change near
bilingual content"* — a measurement that exists because a human **cannot read the output**
and needs a number to sanity-check against.

The kit does not ask the agent to take that measurement. It takes it, before and after, and
reports the delta. Measured strict compliance with long-standing prose instructions runs
around 36% at best configuration, so as prose this happens roughly one time in three and
nobody finds out which. As a hook it happens every time.

That is the whole argument for keeping these rules when the "delete verification
scaffolding" guidance says to cut them: they are gates on the world, not rituals about the
agent's own reasoning.

## Testing

```sh
python3 kit/tests/run_tests.py
```

176 tests, no dependencies. Most of them are negative — what each gate must **refuse** —
because a gate that allows everything passes any happy-path suite.

## CI

Copy `kit/templates/ci-workflow.yml` to `.github/workflows/agentkit.yml`. On a personal
plan CI **detects and cannot block** — branch protection returns 403 without GitHub Pro —
so the workflow's job is to be loud after the fact, not to be a fence.

## What this deliberately does not do

- **No distribution/generation layer.** `ruler` and `rulesync` fan one source out into 30+
  agents' native formats. That is right for a design that duplicates instructions per
  harness and wrong for this one, which has two adapters because `AGENTS.md` is read
  natively by two of three harnesses and the third takes a two-line shim.
- **No re-implementation of agnix.** 445 sourced rules with `verified_on` dates is a higher
  evidentiary standard than anything written here. `verify` calls it and adds only what no
  catalogue covers.
- **No submodule.** Vendored copy with a version stamp, because submodules break on fresh
  clone in exactly the automated contexts this serves. A weaker consistency guarantee,
  traded knowingly.

## Honest note on size

The design estimated the bespoke residue at ~150 lines. `verify/residue.py` came out at
roughly 400, because the budget gates, the amputation vectors, and the frontmatter checks
turned out to need real logic rather than a shell out to agnix. The ratio the estimate was
defending still holds — 445 shared rules reused against ~400 lines written — but the number
was optimistic and is corrected here rather than quietly.
