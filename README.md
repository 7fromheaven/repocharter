# agent-context

**A portable context system for coding agents, and a mechanical layer that enforces it.**

Every coding agent reads a set of instruction files before it does anything. Left alone, that set
grows until you are paying for a novel at the start of every session, and the safety rules inside it
are polite requests the agent honours maybe a third of the time.

This is two things that address that:

1. **A discipline** — exactly two files load at startup, everything else is fetched on demand, and
   the always-loaded set has a hard ceiling that a linter enforces.
2. **`agentkit`** — a dependency-free Python CLI that installs the mechanical parts, compiles your
   safety rules into real interceptors, and refuses to let a repo claim enforcement it has not
   tested.

Works with Claude Code, Codex, and ZCode for context. **Enforcement is Claude Code only today** —
see [Honest limits](#honest-limits), because that asymmetry matters more than anything else here.

---

## Why

The predecessor system worked and cost roughly **50,000 tokens per session before any work began**,
with 38% of its commits being bookkeeping about the documentation itself. Docs about docs.

Investigating why produced three findings, two of which contradicted the obvious diagnosis:

- **Skills were innocent.** All three harnesses lazy-load them. Twelve skills carrying ~48 KB of
  bodies grew the startup prompt by 4,272 characters and contributed **zero** body bytes. Deleting
  them to save cost would have removed the one component already working correctly.
- **The cost was `CLAUDE.md` and its `@`-imports.** Anthropic is explicit that imports load at
  launch, recursive to four hops. A 70-file system wired through imports is an always-loaded system
  wearing a lazy-loading costume. `.claude/rules/*.md` without `paths:` frontmatter loads at launch
  too — that surface alone is **74% of the remaining cost across our fleet.**
- **It is a cost argument, not a quality argument, and we say so.** No vendor has published an
  experiment showing heavy context files degrade agent output. `arXiv:2602.11988` measured that
  context files "do not generally improve task success rates, while increasing inference cost by
  over 20% on average." The honest case for this migration is spend and latency, with adherence
  roughly neutral.

## The shape

```
AGENTS.md                    always-loaded. Rules, not knowledge. Safety first.
CLAUDE.md                    always-loaded. A shim: `@AGENTS.md` and nothing else.
                             ── those two, and no third, ever ──
docs/project/*               named by path in AGENTS.md's routing block. Never imported.
  SPEC.md PLAN.md STATUS.md  Zero startup cost. Read on demand.
  decisions/ pitfalls.md
.agents/skills/<name>/       one procedure each. ~90-100 tokens standing, body on trigger.
.claude/rules/*.md           path-scoped only. Loads when a matching file is read.
.agents/compatibility.json   machine-readable declaration + policy. Never loaded.
```

Four rules hold it together, and each is a **refusal** rather than a feature:

1. **The always-loaded set is fixed at two files.** Growth pressure resolves as deletion or as a
   move to on-demand — never as a third root file. This is the load-bearing one: the old system did
   not fail because any single file was bad, it failed because the set had no ceiling.
2. **Path reference, never `@`-import.** Measured on a real repo: the on-demand corpus was 77,971
   bytes (~20,500 tokens if imported) against **~600 bytes of routing lines**. Same knowledge, 3% of
   the price.
3. **One flat root `AGENTS.md`, never nested.** This is also what makes the byte gate exact.
4. **Nothing append-only.** No journals, no lessons ledgers, no handoff files, no checkpoint
   directories. `STATUS.md` is rewritten as a snapshot; finished plans are deleted, not archived.
   This is what kills the 38%-of-commits problem at its root.

## The budget is real, and it comes from the source

| Gate | Threshold | Kind | Where it comes from |
|---|---|---|---|
| Codex-visible chain | 32,768 bytes | **hard fail** | `DEFAULT_PROJECT_DOC_MAX_BYTES` in Codex's own source |
| Claude-visible repo set | 200 lines | warn | Anthropic's published guidance |

The hard fail sits at the **real shipped default**, not a fraction of it — a gate at an invented
boundary costs you deletions from a safety file to satisfy a fiction. Anthropic publishes no byte
cap, so a qualitative target earns a warning, not a build failure.

Exceed the Codex budget and your safety file is **silently truncated** — content cut mid-file, head
kept, tail dropped, and every later file skipped whole. `agentkit` also checks four separate ways a
repo's own safety file can vanish entirely, including an override filename that replaces it and a
committed config line that shrinks the budget to nothing.

---

## Quick start

Requires Python 3 and nothing else. No install step — the kit is vendored as a directory.

**Look before you touch:**

```sh
kit/agentkit census --repo ~/dev/your-repo    # read-only measurement
kit/agentkit fleet  --root ~/dev              # every repo under a root, one table
python3 kit/tests/run_tests.py                # 176 tests, no dependencies
```

**A fresh repo:**

```sh
kit/agentkit census   --repo .          # measure before anything changes
kit/agentkit apply    --repo . --dry-run
kit/agentkit apply    --repo .          # installs the plumbing; refuses a dirty tree
#   ... author AGENTS.md from kit/templates/AGENTS.md.tmpl yourself ...
#   ... author `policy` in .agents/compatibility.json ...
kit/agentkit apply    --repo .          # recompile permissions from the policy
kit/agentkit self-test --repo .         # fire forbidden calls, assert refusal
kit/agentkit verify   --repo .          # lint; exits non-zero on error
```

**A repo that already has a hand-rolled gate script** adds two steps — harvest its existing rules
into portable policy, then retire the old script only once the new one is proven no weaker:

```sh
kit/agentkit policy scaffold --repo .   # harvest from the repo's own gate
#   ... review policyDraft, then ...
kit/agentkit policy promote  --repo .
kit/agentkit supersede --repo .         # can the old gate be retired safely?
kit/agentkit supersede --repo . --retire
```

**Restart your agent session after `apply`.** Settings are read once at session start, so a session
running from before the install keeps calling the old hooks — and a missing hook script fails
*open*, not closed. This bit us; see [Where we are](#where-we-are).

### What `apply` writes, and what it refuses to write

**Writes** — all mechanical, all regenerable: the declaration file, the vendored gate scripts, the
`hooks` and `permissions.deny` keys in settings (every other key preserved, including your
allow-list), skill symlinks, linter config, and the `CLAUDE.md` shim if absent.

**Never writes:** `AGENTS.md`'s body, or anything under `docs/project/`.

That refusal is deliberate and it is the centre of the design. *A tool that generates truth
recreates the accretion problem in a new costume.* Plumbing can be regenerated forever; the actual
rules have to be written by a person. Copy the template and fill it in.

Everything is idempotent — re-running `apply` on a conforming repo reports `0 change(s)`.

### The commands

| Command | Does |
|---|---|
| `census` | The before-measurement. Without it there is no way to tell if any of this helped. |
| `fleet` | `census` for every repo under a root, one table. |
| `apply` | Install the mechanical layer, idempotently. |
| `policy scaffold` / `promote` | Harvest a draft policy from the repo's own existing rules. |
| `supersede` | Prove the new gate is never weaker, then retire the legacy one. |
| `self-test` | Fire genuinely forbidden calls at the installed hooks and assert refusal. |
| `measure` | Fire every rule the repo *itself* declares at its gate and report which held. |
| `verify` | 445 sourced rules via `agnix`, plus the residue checks no catalogue covers. |
| `migrate` | Mechanical migration plus a written plan for the judgment calls. |
| `revert` | Undo the last mechanical migration from its manifest. |

---

## Enforcement, ranked by whether failure is loud

Most systems lie about this. Controls are ranked by **whether their failure is noticeable**, not by
how much they appear to block.

- **Tier 0 — the capability does not exist in the agent's process.** The only tier with no failure
  mode. A genuinely read-only credential qualifies. Note that some vendors *cannot* be made to
  qualify: if a provider's token API accepts no permission field at any price, and a
  full-privilege token sits in a file owned by the same uid the agent runs as, then "that deploy
  command is blocked" is a regex that does not survive a direct API call.
- **Tier 1 — hooks.** Reachable, but not silently. Five interceptors: shell commands, file writes,
  MCP tool calls, a post-write measurement, and a guard that refuses any config edit which would
  disable hooks or empty the deny list — i.e. it stops the agent disarming its own alarm.
- **Tier 2 — CI detection.** Renamed from "blocking," because on a private repo without a paid plan
  branch protection returns 403. CI is a detector that runs *after* the push it wanted to stop.
- **Tier 3 — the agent enforcing a rule on itself.** The floor, and `AGENTS.md` is required to say
  so. Independent measurement puts best-configuration strict compliance with standing instructions
  at **36.2%**, most frontier models below 25%.

**Everything below Tier 0 fails open.** That is why `self-test` exists: no repo may declare
`enforcement: blocking` until it has fired a forbidden call at its own hooks and watched the
refusal. An untested gate is a guess.

`self-test` covers the universal rules. **`measure` covers the rules a repo writes for
itself** — which is where every inert-fence defect has lived. It builds a probe that provably
matches each rule's own pattern, fires it at the installed gate, and compares the verdict to
the verb declared. Three things keep it from becoming the next detector that cried wolf: a
rule whose conditions cannot hold together is **INERT** (demonstrated, not suspected); a rule
it cannot build a probe for is **UNMEASURED and never reported as passing**; and an
independent corpus grouped by danger catches a pattern that is live but simply *wrong*, which
pattern-derived probes match by construction and can never detect.

Current fleet reading: **121 of 127 declared fences fire as declared, 0 broken, 0 over-broad**,
6 unmeasured and reported as such.

Gate scripts **fail closed** — a gate that cannot parse its input, read its policy, or compile a
pattern exits 2. This is not pedantry: Claude Code treats exit 1 as *non-blocking and proceeds*. An
ancestor of our shell gate exited **0** when `jq` was missing, which was a silent allow of every
command on the machine, and nothing noticed for months. There is a regression test for exactly that.

### The idea worth stealing: measure, don't ask

Some rules exist because a human **cannot check the output themselves** — "report string lengths in
a script the operator does not read, before and after any change near it."

As prose, that instruction runs at roughly the 36% compliance figure above. It happens one time in
three and nobody finds out which times. So the kit does not ask the agent to take the measurement.
**The hook takes it, before and after, and reports the delta.** Every time, mechanically.

That is also the principled line for what to keep when told to delete verification scaffolding:
rules that check *the world* stay; rules that are rituals about the agent's own reasoning go.

---

## Where we are

**Working and used daily.** `agentkit 0.2.0`, 176 tests, zero dependencies, verified from a fresh
clone. Six repositories migrated out of a nineteen-repo fleet.

| repo | always-loaded before | after |
|---|---:|---:|
| A | 103,354 | 6,045 |
| B | 38,390 | 7,165 |
| C | 33,595 | 7,878 |
| D | 22,602 | 7,346 |
| E | 7,777 | 7,777 — had converged independently |
| F | 11,470 | 11,787 — layout was already right; **enforcement** was missing |

Five landed between 6,045 and 7,878 bytes, and two of those had arrived there independently before
this work. That consistency is the design's shape asserting itself rather than arithmetic.

Thirteen repos remain, 354,597 bytes, three-quarters of it unscoped rules files.

### What is not working, stated plainly

Three inert-fence bugs surfaced in a single week, and none was caught by the verifier whose entire
job that is:

1. **53 dead fences shipped across all six migrated repos.** The policy harvester read shell `||` as
   `&&`, turning a rule with *alternatives* into a conjunction that **can never fire** while looking
   exactly like a working fence — covering production deploys, write-credential strings, and
   database pushes. Found by a colleague, not by us. Fixed, with the joiner now read from the
   source, and a `policy repair` command to split already-shipped ones.
2. **The detector written to catch that then produced 9 false positives**, calling working fences
   broken. It now warns rather than errors and states that it is heuristic.
3. **`apply` emits a deny rule the harness does not enforce** — caught by a startup warning from
   Claude Code itself, minutes after `verify` returned zero errors on the same repo.

Additionally: a migration that rewires hooks **invalidates every running agent session**, and stale
sessions fail open — every command in them proceeds ungated, with only a scrolling warning to say
so. Nothing in the kit currently tells you to restart.

We keep these in the README rather than in a private issue tracker because the entire premise of
this system is that a declaration of enforcement nobody measured is worse than no declaration at
all — and the system committed that error itself, on the same day one repo's `AGENTS.md` described
those exact dead rules as "mechanically enforced."

---

## What's planned

**Now — earn back trust in the fences.**
Re-measure every enforcement claim in every migrated repo by running the command it names. One repo
has a script that proves its own claims; the other five need an equivalent. Ship the deny-rule fix
and a residue check that catches the whole class.

**Next — make repos 7-19 cheap.**
Six mechanical steps are still done by hand on every migration. Six worked examples now exist, which
is enough to fold them into `migrate --apply`. This is the highest-value remaining work.

**Then — the fresh-repo story.**
`apply` already does the complete mechanical job on a clean repo; every remaining manual step is
migration-specific. The gap is policy: `policy scaffold` is a *harvester*, so a fresh repo with no
existing gate harvests nothing and lands in an empty-policy state — five hooks installed, enforcing
nothing, `self-test` passing because it only exercises the universal rules. Green checkmark, no
fence. The fix is the inverse of a harvester: **a starter policy library by stack** shipping
opinionated fences on day one. This is the main blocker to anyone using this out of the box.

**Then — make the portability claim true for safety, not just context.**
Enforcement is Claude Code only. A second harness's interceptors would close the gap between what
the context layer promises and what the fence delivers.

**Open, not blocking:** one safety tier rests on OS-owned config that has never been tested on our
machines and remains an assertion until it passes a deny-and-observe test. And this repo has never
had `agentkit` applied to itself. It should dogfood.

---

## Honest limits

- **"Portable" describes context, not enforcement.** `AGENTS.md` is read natively by two harnesses
  and by the third through a two-line shim — that part is real. All five interceptors are Claude
  Code hooks. Under the others the production boundary is **advisory**, and a rule believed to be
  enforced when it is not is worse than one known to be advisory.
- **`supersede` handles shell gates only.** Retiring other validator types deserves the same replay
  proof and does not have it yet.
- **No distribution or generation layer.** Tools that fan one source out into 30+ agents' native
  formats are right for a design that duplicates per harness and wrong for this one, which converges
  on a single file with one shim.
- **No re-implementation of `agnix`.** 445 sourced rules with verification dates is a higher
  evidentiary standard than anything we would write. `verify` calls it and adds only the checks no
  catalogue covers — notably the amputation vectors, which we tested `agnix` against and measured at
  zero detections.
- **Vendored, not packaged.** A copy per repo with a version stamp. Submodules break on fresh clone
  in exactly the automated contexts this serves; that is a weaker consistency guarantee, traded
  knowingly.
- **The residue checks came out at ~400 lines against a ~150-line estimate.** The reuse ratio the
  estimate defended still holds — 445 shared rules against ~400 lines written — but the number was
  optimistic and is corrected here rather than quietly.

---

## Layout

```
README.md          this file — the design, the commands, and the honest limits
kit/agentkit       the CLI. Python 3, no dependencies.
kit/hooks/         the five interceptors, vendored into each repo by `apply`
kit/lib/           harvest, migrate, supersede, measure
kit/verify/        the residue checks — what no shared catalogue covers
kit/schema/        the JSON Schema for .agents/compatibility.json
kit/templates/     AGENTS.md skeleton, linter config, CI workflow
kit/tests/         176 tests, no dependencies, mostly negative
```

**Evidence standard.** Every measurement quoted here was taken rather than cited: byte budgets read
from the harness's own source, gate behaviour recorded by firing commands at the installed hook,
adherence figures taken from published experiments with the reasoning stated. Where a number is
inferred it says so. Several vendor documentation claims were refuted by reading the shipped source.

**Provenance.** This was extracted from a private repository where it runs across a fleet of
production projects. The migration history, per-repository measurements and open decisions stay
there; what ships here is the tool and the reasoning behind it.
