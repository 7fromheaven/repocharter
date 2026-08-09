# RepoCharter

**One repo charter. Every coding agent.**

[repocharter.com](https://repocharter.com)

RepoCharter is a dependency-free toolkit for repositories shared across Claude Code, Codex,
OpenCode, Hermes Agent, ZCode, and other coding-agent harnesses. It keeps project guidance in one
place, loads detailed context only when it is needed, and wires one safety policy into the native
controls each supported harness actually exposes.

With RepoCharter, you get:

- **One source of truth** instead of separate instruction sets for every agent.
- **Lean startup context** so more of the prompt is available for the work itself.
- **Provider-native guardrails** for shell commands, file writes, MCP tools, and configuration.
- **Proof that the guardrails run** before a repository reports verified enforcement.
- **A safe migration path** that preserves existing hooks, settings, and project knowledge.
- **No service to run and no package to install**—just Python 3 and files committed with your repo.

> Current version: **RepoCharter 0.3.0** · CLI: `kit/agentkit`
>
> **204 tests** · **zero runtime dependencies**

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

## Provider support

| Harness | Shared context | Safety integration | Enforcement status |
|---|---|---|---|
| **Codex** | Native `AGENTS.md` and `.agents/skills/` discovery | Project hooks for Bash, `apply_patch`, MCP, and post-write measurement | Eligible for **verified enforcement** after exact-hook trust and live deny/allow proof |
| **Claude Code** | `CLAUDE.md` and skill symlinks reach the same canonical files | Hooks for Bash, writes, MCP, config changes, and post-write measurement | **Advisory** until provider-backed proof is recorded |
| **OpenCode** | Native `AGENTS.md` and `.agents/skills/` discovery | No RepoCharter tool-call adapter yet; pre-commit verification still applies | **Advisory** |
| **Hermes Agent** | Native `AGENTS.md`; canonical skills require an external-directory setting | No RepoCharter tool-call adapter yet; pre-commit verification still applies | **Advisory** |
| **ZCode** | Native `AGENTS.md`; project skill imported as a symlink | No live-tested safety adapter yet | **Advisory** |
| **Other harnesses** | Read `AGENTS.md` and canonical skill paths directly | Explicit fallback until an adapter exists | **Advisory** |

**Verified enforcement** means live probes showed the harness stopping tested prohibited calls
before execution while allowing benign calls. The compatibility manifest serializes this state as
`enforcement: "blocking"`; that internal value does not mean RepoCharter or the repository is
blocked. Evidence is scoped to the exact checkout, adapter hash, harness version, and probe matrix.

RepoCharter does not pretend every provider exposes the same control surface. Context portability
and safety enforcement are reported separately. OpenCode and Hermes Agent can use local or
self-hosted models; model location does not upgrade advisory integration into verified enforcement.

---

## Quick start

RepoCharter requires Python 3 and nothing else. Clone this repository, then run the CLI directly from
the checkout.

### 1. Measure before changing anything

```sh
kit/agentkit census --repo ~/dev/your-repo
```

This captures the existing startup context and migration state so you can review the change against
a real baseline.

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
persisted project-hook trust is active. See the
[official Codex hook documentation](https://learn.chatgpt.com/docs/hooks) for the trust model.

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
pause for an interactive `ask`; Codex currently translates the same decision to a denial because
its `PreToolUse` hook cannot initiate approval safely.

Hooks are guardrails, not an operating-system security boundary. Credentials and production
capabilities should still use the narrowest permissions their providers offer. Verified
enforcement covers the tested provider paths and probe matrix; it is not an exhaustive proof
against every alternate command spelling or access path.

---

## Bringing an existing repository across

RepoCharter is designed to migrate a real repository without flattening the controls it already
has.

```sh
kit/agentkit census --repo ~/dev/your-repo
kit/agentkit apply --repo ~/dev/your-repo --dry-run
kit/agentkit apply --repo ~/dev/your-repo
```

If the repository has a hand-written shell gate, RepoCharter can harvest a reviewable policy draft
and compare the old and new gates before retiring anything:

```sh
cd ~/dev/your-repo
kit/agentkit policy scaffold --repo .
# Review policyDraft in .agents/compatibility.json.
kit/agentkit policy promote --repo .
kit/agentkit apply --repo .
kit/agentkit supersede --repo .
kit/agentkit supersede --repo . --retire
```

`supersede` retires the legacy shell gate only when the replacement is no weaker on its probe
corpus, does not block benign commands, and has no unresolved harvested rules. Retirement is
quarantined and reversible.

For broader legacy layouts, `migrate` performs the mechanical half and writes out the judgment
calls instead of guessing:

```sh
kit/agentkit migrate --repo .
kit/agentkit migrate --repo . --apply
```

---

## CLI reference

| Command | Purpose |
|---|---|
| `census` | Measure context and migration state before touching the repository |
| `fleet` | Run the census across repositories under one root |
| `apply` | Install or update the mechanical layer idempotently |
| `policy scaffold` | Harvest a reviewable draft from an existing repository gate |
| `policy promote` | Move reviewed draft rules into live policy |
| `supersede` | Compare and safely retire a legacy shell gate |
| `self-test` | Exercise universal allow/deny behavior; optionally prove live Codex hooks |
| `measure` | Fire every declared repository rule and report real coverage |
| `verify` | Validate schema, adapters, budgets, residue, and repository checks |
| `migrate` | Apply safe mechanical migration and surface judgment calls |
| `revert` | Restore the last quarantined mechanical migration |

All default verification is offline and dependency-free. `verify --agnix` optionally adds the
network-fetched shared rule catalogue.

---

## Safe by default

- **Dirty worktrees are refused** unless you explicitly pass `--allow-dirty`.
- **Existing hooks and unrelated settings are preserved**, including user allow-lists.
- **`AGENTS.md` and `docs/project/` remain human-owned.**
- **Every install is idempotent**; a conforming repository reports zero changes.
- **Pre-commit verification fails closed** if Python, the vendored CLI, or a declared validation
  command is missing.
- **Provider claims remain explicit**: context support does not masquerade as safety enforcement.

## Current status

RepoCharter 0.3.0 is used in production repositories today. Its 204-test suite is intentionally
heavy on negative cases: dangerous calls must be refused, malformed inputs must fail closed,
foreign configuration must survive, and a gate that never ran must not report success.

The Codex adapter has provider-backed Bash, `apply_patch`, MCP, allow-path, and persisted-trust
evidence. Each checkout still has to review and prove its own exact hook hash. Claude Code uses the
same policy engine but remains advisory until equivalent provider-backed evidence is recorded.
OpenCode, Hermes Agent, and ZCode currently provide portable context without RepoCharter tool-call
enforcement.

## Honest limits

- Context is portable; enforcement is always provider- and checkout-specific.
- RepoCharter cannot make a broadly privileged credential read-only. Use provider-side least
  privilege.
- OpenCode, Hermes Agent, ZCode, and generic harnesses remain advisory until live-tested safety
  adapters exist.
- `supersede` compares shell gates; other legacy validator types require manual review.
- The toolkit is vendored into each repository rather than installed globally or delivered as a
  service.
- RepoCharter does not synchronize credentials, connector authorization, environment files, or
  customer data.

---

## Repository layout

```
README.md          this file — the design, the commands, and the honest limits
kit/agentkit       the RepoCharter CLI (compatibility name). Python 3, no dependencies.
kit/hooks/         Claude and Codex adapters over one policy engine
kit/lib/           harvest, migrate, supersede, measure
kit/verify/        the residue checks — what no shared catalogue covers
kit/schema/        the JSON Schema for .agents/compatibility.json
kit/templates/     AGENTS.md skeleton, linter config, CI workflow
kit/tests/         204 tests, no dependencies, mostly negative
```

**Ready to try RepoCharter?** Start with `kit/agentkit census --repo ~/dev/your-repo`. It is
read-only, takes seconds, and gives you a concrete baseline before RepoCharter changes a file.
