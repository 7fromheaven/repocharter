# RepoCharter

**One repo charter. Every coding agent.**

[repocharter.com](https://repocharter.com)

RepoCharter gives every coding agent in your repository the same map: what the project is, how work
gets done, and which boundaries must not be crossed. It works across Claude Code, Codex, OpenCode,
Hermes Agent, ZCode, and other coding-agent harnesses while keeping startup context lean and
turning critical rules into testable guardrails wherever a harness exposes the controls.

With RepoCharter, you get:

- **One source of truth** instead of separate instruction sets for every agent.
- **Lean startup context** so more of the prompt is available for the work itself.
- **Provider-native guardrails** for shell commands, file writes, MCP tools, and configuration.
- **Proof that the guardrails run** before a repository reports verified enforcement.
- **Low-risk adoption** that previews every change and preserves existing hooks, settings, and
  project knowledge.
- **No service to run and no package to install**—just Python 3 and files committed with your repo.

> Current version: **RepoCharter 0.3.7** · CLI: `kit/agentkit`
>
> **374 tests** · **zero runtime dependencies**

RepoCharter is the product name. The 0.3.x executable remains `kit/agentkit` so existing
repositories and automation keep working. The old name is a compatibility-facing CLI identifier,
not a second product.

---

## Built for everyday agent work

**Switch agents without rewriting the repository.** Claude Code, Codex, and other harnesses can
reach the same instructions, skills, project state, and decisions through small, explicit adapters.

**Keep the prompt focused.** Working rules stay in one compact startup document. Specifications,
plans, decisions, research, and procedures are named by path and loaded on demand.

**Turn critical boundaries into mechanisms.** Rules such as “do not edit generated files,” “do not
deploy production through MCP,” or “ask before discarding work” can be expressed once and enforced
through the harness surfaces that support them.

**Know what is actually active.** RepoCharter checks the installed configuration, fires real probes
at declared policies, and records provider-backed evidence. An installed hook is not treated as a
working hook merely because the file exists.

**Adopt it without surrendering your repo.** RepoCharter writes plumbing, not project truth. It never
generates the body of `AGENTS.md` and never rewrites anything under `docs/project/`.

---

## One source of truth

```text
AGENTS.md                    authored working rules; the canonical startup document
CLAUDE.md -> AGENTS.md       relative compatibility symlink when no shim already exists

docs/project/
  SPEC.md                    stable product truth and locked constraints
  PLAN.md                    active work and validation gates
  STATUS.md                  current verified state and next action
  decisions/                 one durable decision per file
  pitfalls.md                capped, verified negative results

.agents/skills/<name>/       canonical on-demand procedures
.claude/skills/<name>        relative symlinks to the canonical skills
.agents/compatibility.json   provider adapters, budgets, policy, and evidence
.codex/hooks.json            Codex project-hook adapter
```

The result is deliberately simple: one authored instruction file, one policy, and one durable
project-memory layout. Provider-specific files are adapters, not competing sources of truth.

Claude auto memory is disabled by default. RepoCharter already gives learned facts a reviewed,
portable home under `docs/project/`; a hidden, machine-local `MEMORY.md` would duplicate that system
and add startup context that other agents and CI cannot see. A repository may explicitly opt in by
setting `autoMemory: "on"` and recording a nonempty `autoMemoryReason`, but auto memory never becomes
authoritative project state.

## Provider support

| Harness | Shared context | Safety integration | Enforcement status |
|---|---|---|---|
| **Codex** | Native `AGENTS.md` and `.agents/skills/` discovery | Project hooks for Bash, `apply_patch`, MCP, and post-write measurement | Eligible for **verified enforcement** after exact-hook trust and live deny/allow proof |
| **Claude Code** | `CLAUDE.md` and skill symlinks reach the same canonical files | Hooks for Bash, writes, MCP, config changes, and post-write measurement | Eligible for **verified enforcement** after live deny/allow proof in the checkout |
| **OpenCode** | Native `AGENTS.md` and `.agents/skills/` discovery | No RepoCharter tool-call adapter yet; pre-commit verification still applies | **Advisory** |
| **Hermes Agent** | Native `AGENTS.md`; canonical skills require an external-directory setting | No RepoCharter tool-call adapter yet; pre-commit verification still applies | **Advisory** |
| **ZCode** | Native `AGENTS.md`; project skill imported as a symlink | No live-tested safety adapter yet | **Advisory** |
| **Other harnesses** | Read `AGENTS.md` and canonical skill paths directly | Explicit fallback until an adapter exists | **Advisory** |

**Verified enforcement** means live probes showed the harness stopping tested prohibited calls
before execution while allowing benign calls. The compatibility manifest serializes this state as
`enforcement: "blocking"`; that internal value does not mean RepoCharter or the repository is
blocked. The portable manifest record is paired with a private attestation under this worktree's
absolute Git directory, so a clone or sibling worktree cannot inherit the checkout claim. Evidence
is scoped to the exact checkout, adapter hash, harness version, and probe matrix.

RepoCharter does not pretend every provider exposes the same control surface. Context portability
and safety enforcement are reported separately. OpenCode and Hermes Agent can use local or
self-hosted models; model location does not upgrade advisory integration into verified enforcement.

---

## Quick start

RepoCharter requires Python 3 and nothing else. Clone this repository, then run the CLI directly from
the checkout.

### Agent-operated migration or upgrade

Open your coding agent in the RepoCharter checkout. The distribution exposes its bundled workflow
through each provider's native skill picker before anything is installed in the target repository.
Name the target explicitly:

```text
Codex:      $migrate-repocharter Migrate /absolute/path/to/repository and stop before external actions.
Claude Code: /migrate-repocharter Migrate /absolute/path/to/repository and stop before external actions.
```

The workflow resolves that target, moves into its exact checkout, and owns the state transitions.
For another harness, or when starting the agent inside the target instead, use the portable path
form:

> Migrate this repository to RepoCharter using
> `<repocharter-checkout>/kit/skills/migrate-repocharter/SKILL.md`. Own the migration end to end,
> preserve existing safety gates until their replacements are proved, and stop before push, merge,
> deploy, publish, or bug submission unless I authorize those separately.

The workflow checkpoints the exact target checkout, resumes interrupted work, detects Codex's
current linked-worktree hook-discovery defect before promotion, preserves dirty migration state in
an independent clone when needed, and promotes only stale providers. `apply` installs the same
workflow in the target for future resumes and upgrades. The manual steps below remain the auditable
protocol the skill executes; they are not a prompt-shuttling requirement for the operator.

### 1. Measure before changing anything

```sh
kit/agentkit census --repo ~/dev/your-repo
```

This captures the existing startup context and agent configuration so you can review the change
against a real baseline.

### 2. Preview and apply the mechanical layer

```sh
kit/agentkit apply --repo ~/dev/your-repo --dry-run
kit/agentkit apply --repo ~/dev/your-repo
```

`apply` refuses a dirty worktree by default, preserves unrelated Claude and Codex configuration,
and reports every file it changes.

Once applied, RepoCharter is vendored into the target repository:

```sh
cd ~/dev/your-repo
kit/agentkit verify --repo .
```

### 3. Add your project context and policy

Use `kit/templates/AGENTS.md.tmpl` as a starting point when the repository does not already have
an `AGENTS.md`. Write the project-specific rules yourself; RepoCharter will not invent them.

Then review `.agents/compatibility.json` and add only boundaries that matter to this repository.
The `policy` portion might look like this:

```json
{
  "policy": {
    "protectedBranches": ["main"],
    "denyWritePaths": [
      {
        "glob": "generated/**",
        "reason": "Generated output must be changed through its source."
      }
    ],
    "denyMcpTools": [
      {
        "pattern": "^mcp__.*__deploy_to_production$",
        "reason": "Production deployment is an operator-run action."
      }
    ],
    "measureOnWrite": []
  }
}
```

Re-run `apply` after changing policy so provider-native permissions are recompiled:

```sh
kit/agentkit apply --repo .
```

### 4. Prove the installation

```sh
kit/agentkit self-test --repo .
kit/agentkit measure --repo .
kit/agentkit verify --repo .
```

- `self-test` checks the universal safety floor with both forbidden and benign calls.
- `measure` fires every repository-declared rule and reports enforced, broken, or unmeasured.
- `verify` checks schema, adapters, context budgets, residue, and declared validation commands.

### 5. Activate Codex project hooks

Codex requires a one-time review whenever project-hook definitions change:

1. Restart Codex in the target repository.
2. Open `/hooks`.
3. Review and trust the exact definitions from `.codex/hooks.json`.
4. Run the live promotion:

```sh
kit/agentkit self-test --repo . --promote-codex
kit/agentkit verify --repo . --effective
```

Promotion succeeds only after disposable deny/allow probes and an ordinary run confirm that
persisted project-hook trust is active. Preserved foreign hooks are never dropped for the probe:
RepoCharter copies statically referenced checkout-local scripts into the disposable checkout and
binds their bytes into the attestation. A missing, external, or dynamic implementation fails
promotion clearly; do not remove or rewrite that hook merely to make promotion green. See the
[official Codex hook documentation](https://learn.chatgpt.com/docs/hooks) for the trust model.

### 6. Earn the same claim under Claude Code

Claude Code reads hooks once, at session start, so restart it after `apply`. Start `claude`
interactively in the checkout once, review the project, and accept its trust prompt (a trusted
parent workspace also covers descendants). Then:

```sh
kit/agentkit self-test --repo . --promote-claude
```

This drives the real `claude` executable in a disposable repository and requires both directions on
Bash, Write, and MCP. The intended tool call and matching hook response must appear in structured
event output; each denial must carry RepoCharter's own reason and leave its marker uncreated, while
each benign call must actually run. A second call uses this checkout's effective user, project, and
local settings. Promotion separately checks persisted workspace trust and writes a private
checkout attestation. Every probe tool is pre-allowed and the probe repository's
`permissions.deny` is emptied, so a native permission refusal cannot be mistaken for a hook denial.

The ConfigChange guard is verified in a disposable fixture rather than scored as a live provider
event. Ordinary mode can refuse writes before the hook, but bypass-authorized writes are possible;
the decisive measurement is that headless `claude -p` sessions emitted no ConfigChange event for
authorized Write, Bash, or external settings mutations. The fixture checks the provider-shaped
payload and the exact five installed handler mappings. `verify` independently refuses
`disableAllHooks`.

---

## What RepoCharter enforces

The universal policy provides a conservative floor. Repository policy adds the boundaries no
generic tool can infer, such as production deploys, credential surfaces, generated files, and
live-data operations.

| Surface | Claude Code | Codex | Examples |
|---|---|---|---|
| Shell | `Bash` hook | `Bash` project hook | force-push, checks bypass, destructive Git and database commands |
| File writes | Edit/Write hooks and permissions | `apply_patch` project hook | protected paths and agent configuration |
| MCP | `mcp__.*` hook | `mcp__.*` project hook | production deploys or argument-scoped tool denials |
| Post-write | Edit/Write hook | `apply_patch` project hook | measurements the operator needs after a change |
| Configuration | `ConfigChange` guard | write guard | disabling or replacing the installed safety layer |

Gate scripts fail closed when they cannot parse input, read policy, or compile a pattern. Claude can
pause directly on an interactive `ask`. Codex's `PreToolUse` hook cannot create that prompt, so an
approval-capable turn hands the call to Codex's native permission flow and a no-approval turn denies
it. The agent must submit confirmation-class calls for native approval; unconditional `deny` rules
remain blocking either way.

Hooks are guardrails, not an operating-system security boundary. Credentials and production
capabilities should still use the narrowest permissions their providers offer. Verified
enforcement covers the tested provider paths and probe matrix; it is not an exhaustive proof
against every alternate command spelling or access path.

---

## Add RepoCharter to an existing repository

RepoCharter is designed for repositories with history. It installs its mechanical layer without
replacing the instructions, project knowledge, hooks, or unrelated provider settings already
there.

```sh
kit/agentkit census --repo ~/dev/your-repo
kit/agentkit apply --repo ~/dev/your-repo --dry-run
kit/agentkit apply --repo ~/dev/your-repo
```

`census` is read-only. `apply --dry-run` previews the footprint, and `apply` reports every file it
changes. This is the normal adoption path; no previous context system is required.

RepoCharter also retains specialized compatibility tools for a recognized legacy context layout
and hand-written Claude shell gates. They are not universal importers for arbitrary agent-memory or
context systems, and most users can ignore them. Their exact scope and reversible workflow are in
the [mechanical reference](kit/README.md#only-when-replacing-a-supported-legacy-system).

---

## CLI reference

| Command | Purpose |
|---|---|
| `census` | Inspect startup context and existing agent configuration before changing anything |
| `apply` | Install or update the mechanical layer idempotently |
| `self-test` | Exercise universal allow/deny behavior; optionally prove live Codex hooks |
| `measure` | Fire every declared repository rule and report real coverage |
| `verify` | Validate schema, adapters, budgets, residue, and repository checks |
| `fleet` | Run the census across repositories under one root |

These are the commands most users need. The mechanical reference documents the specialized legacy
commands separately. All default verification is offline and dependency-free. `verify --agnix`
optionally adds the network-fetched shared rule catalogue.

---

## Safe by default

- **Dirty worktrees are refused** unless you explicitly pass `--allow-dirty`.
- **Existing hooks and unrelated settings are preserved**, including user allow-lists.
- **Claude auto memory defaults off**; an explicit opt-in must record why its provider-local
  scratchpad is appropriate for that repository.
- **`AGENTS.md` and `docs/project/` remain human-owned.**
- **Every install is idempotent**; a conforming repository reports zero changes.
- **Pre-commit verification fails closed** if Python, the vendored CLI, or a declared validation
  command is missing.
- **Existing pre-commit hooks stay reachable**: the managed verifier runs before legacy hook
  control flow, and `verify` rejects a block hidden behind an earlier `exit`.
- **Provider claims remain explicit**: context support does not masquerade as safety enforcement.

## Current status

RepoCharter is used in production repositories today. Its 322-test suite is intentionally
heavy on negative cases: dangerous calls must be refused, malformed inputs must fail closed,
foreign configuration must survive, and a gate that never ran must not report success.

The Codex adapter has provider-backed Bash, `apply_patch`, MCP, allow-path, and persisted-trust
evidence. Each checkout still has to review and prove its own exact hook hash. The Claude Code
adapter has provider-backed Bash, Write, and MCP deny-and-allow evidence, structured hook-event
proof, an observed post-write measurement, persisted covering trust, and a target effective-settings
probe. Both providers pair the tracked digest/version record with a checkout-private Git
attestation. ConfigChange's handler logic and exact wiring are fixture-verified because the
headless promotion harness emits no watcher event for settings mutations. OpenCode, Hermes Agent,
and ZCode currently provide portable context without RepoCharter tool-call enforcement.

## Honest limits

- Context is portable; enforcement is always provider- and checkout-specific.
- RepoCharter cannot make a broadly privileged credential read-only. Use provider-side least
  privilege.
- OpenCode, Hermes Agent, ZCode, and generic harnesses remain advisory until live-tested safety
  adapters exist.
- The specialized migration utilities recognize a particular legacy directory layout and a narrow
  hand-written Claude shell-gate shape; they are not universal importers for arbitrary context or
  memory systems.
- `supersede` compares supported shell gates; other validator types require manual review.
- The toolkit is vendored into each repository rather than installed globally or delivered as a
  service.
- RepoCharter does not synchronize credentials, connector authorization, environment files, or
  customer data.

## License and contributions

RepoCharter is licensed under the [Apache License 2.0](LICENSE). Copyright for
individual contributions remains with their respective contributors. Unless explicitly stated
otherwise, contributions submitted for inclusion are licensed under Apache-2.0 as described in
section 5 of the license.

Portions of the Bash safety policy were adapted from
[ctx-fieldbook](https://github.com/armenr/ctx-fieldbook), created by **Armen Rostamian**, and are
used and relicensed with permission. See [NOTICE](NOTICE) for the durable
attribution carried by redistributed copies.

---

## Repository layout

```
README.md          this file — the design, the commands, and the honest limits
kit/agentkit       the RepoCharter CLI (compatibility name). Python 3, no dependencies.
kit/skills/        bundled workflow bodies; one canonical copy of each procedure
.agents/skills/    Codex bootstrap link to the migration workflow
.claude/skills/    Claude Code bootstrap link through the canonical skill path
kit/hooks/         Claude and Codex adapters over one policy engine
kit/lib/           harvest, migrate, supersede, measure
kit/verify/        the residue checks — what no shared catalogue covers
kit/schema/        the JSON Schema for .agents/compatibility.json
kit/templates/     AGENTS.md skeleton, linter config, CI workflow
kit/tests/         374 tests, no dependencies, mostly negative
LICENSE            Apache License 2.0
NOTICE             contributor and upstream attribution
```

**Ready to try RepoCharter?** Start with `kit/agentkit census --repo ~/dev/your-repo`. It is
read-only, takes seconds, and gives you a concrete baseline before RepoCharter changes a file.
