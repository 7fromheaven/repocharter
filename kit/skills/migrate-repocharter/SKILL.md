---
name: migrate-repocharter
description: Migrate, upgrade, resume, promote, verify, or integrate RepoCharter in a repository. Use when adopting RepoCharter, updating its vendored kit, recovering an interrupted migration, proving Claude Code or Codex enforcement, handling Codex hooks in a linked worktree, or finishing a prepared migration branch.
---

# Migrate RepoCharter

Own the migration end to end. Measure the checkout first, resume from the first incomplete
state, and return one compact handoff. Do not make the operator shuttle prompts or reconstruct
provider state.

## Resolve the target

Resolve the target repository before running the checkpoint.

- An explicit repository path in the request is the target.
- Without an explicit path, use the current checkout only when the skill is installed in that
  consumer repository.
- When invoked from a RepoCharter source distribution, do not treat the distribution checkout as
  the migration target. If the request does not name a target, ask for its path before writing.

Resolve the target to its absolute Git toplevel and use that exact path for every command until a
standalone route replaces it. Use the workflow under `kit/skills/migrate-repocharter/` when running
from a RepoCharter distribution; use `.agents/skills/migrate-repocharter/` when running an installed
consumer's own copy.

## Start or resume

Run the checkpoint from this skill before choosing an action:

```sh
python3 <workflow-path>/scripts/checkpoint.py --repo <target-repo> --effective
```

Treat the JSON-compatible checkpoint as current truth; do not infer state from an earlier
transcript.

Follow its route:

- `in-place`: continue in the exact checkout.
- `provider-access-blocked`: stop provider work and rerun the checkpoint in an environment where
  the named provider can access its runtime state. For Codex, the process must be able to read and
  write the configured state directory (normally `~/.codex`). Preserve the reported diagnostic;
  do not promote while `hooks/list` is indeterminate.
- `standalone-clone`: create an independent normal clone with the mode named by the checkpoint:

  ```sh
  python3 <workflow-path>/scripts/prepare_standalone.py \
    --repo <target-repo> --dest <private-absolute-path> --mode <checkpoint-mode>
  ```

  `clean-head` leaves unrelated staged, unstaged, and untracked work only in the untouched source
  and gives `apply` a clean base. `preserve-state` is reserved for reviewed, already-prepared
  migration changes when linked-worktree recovery must transfer them byte-for-byte. The agent, not
  the operator, reviews that distinction. Continue only in the resulting normal clone. Never use a
  hook-trust bypass, `--allow-dirty`, stash, reset, restore, or clean as recovery.

## Migration state machine

1. Read the repository's own `AGENTS.md`, current project status, plan, relevant decisions, and
   pitfalls. Inspect legacy hooks and policy before changing either.
2. On a clean starting tree, run the selected distribution's RepoCharter `apply` against the
   target. If unrelated work exists, use the standalone helper in `clean-head` mode; do not stack
   migration plumbing over it.
3. Scaffold repository policy while legacy gates are still active. Fire every declared fence with
   `repocharter measure`; retire a legacy safety gate only after the replacement is proven no weaker.
4. Reach the mechanical fixed point: `apply --dry-run` reports zero changes, strict verification
   passes, measurement passes, and every validation command declared by the repository passes.
5. Use the checkpoint's provider states. Promote only a provider whose `promotionRequired` is
   true, and never while its `providerAccessBlocked` or `providerRecoveryRequired` field is true.
   Runtime unavailability, missing discovery, and wrong-checkout discovery are not evidence of
   stale trust. A provider marked current has matching adapter bytes, CLI version, checkout-local
   attestation, effective settings, and—under Codex—the exact trusted project hook source.
6. After any provider promotion, run one final fixed-point, strict/effective verification,
   measurement, and repository validation pass. Commit only the migration branch unless the user
   separately authorized integration.

Do not update durable status to say committed, pushed, merged, deployed, or healthy until that
event has succeeded. A branch switch or documentation-only commit in the same checkout does not
invalidate byte-bound provider evidence; rerun the checkpoint instead of reflexively promoting.

## Provider promotion

Codex promotion is valid only when the checkpoint reports the exact target
`<checkout>/.codex/hooks.json`. If `hooks/list` fails, times out, returns no response, or cannot
initialize Codex's state runtime, use the `provider-access-blocked` recovery first; promotion cannot
repair an unreadable or unwritable provider state directory. If Codex reports zero hooks in a linked
worktree or reports a hook from the primary checkout, review that every uncommitted path belongs to
the prepared migration and use the `preserve-state` standalone route. Do not repeat trust restarts
after the exact persisted-trust test has reproduced the defect.

When promotion is actually required:

- Codex: start a fresh trusted session in the target checkout, review the current definitions in
  `/hooks`, then run `repocharter self-test --repo . --promote-codex`.
- Claude Code: start a fresh session in the target checkout, establish workspace trust, then run
  `repocharter self-test --repo . --promote-claude`.

An approval-capable agent may operate both installed provider CLIs. Provider identity is not the
boundary; the target checkout, fresh settings, native trust, and live deny/allow evidence are.

## Integration lane

Keep integration separate from migration. Only after explicit push/merge/deploy authorization:

1. Fetch the canonical remote branch once and refuse non-fast-forward concurrency.
2. Integrate the reviewed migration commit without rewriting history.
3. Rerun the checkpoint. If provider evidence is current, do not run live probes again. Run one
   final fixed-point, strict/effective verification, measurement, and validation pass.
4. Push once. Wait once for the deployment tied to the pushed commit, then perform one bounded
   health check. Avoid full metadata dumps when status and commit identity are sufficient.
5. Rewrite durable status with what actually happened and any checkout still needing local
   reconciliation.

Never infer authorization to push, merge, deploy, publish, submit a provider bug, or modify user
configuration. Pause only for an unavoidable native trust action or a genuinely new external
authorization boundary.

## Final report

Report: target checkout and route, installed version, policy measurement, provider states,
validation results, local commit, external actions actually completed, and the one next action.
Do not narrate every command.
