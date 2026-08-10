---
name: migrate-repocharter
description: Migrate, upgrade, resume, promote, verify, or integrate RepoCharter in a repository. Use when adopting RepoCharter, updating its vendored kit, recovering an interrupted migration, proving Claude Code or Codex enforcement, handling Codex hooks in a linked worktree, or finishing a prepared migration branch.
---

# Migrate RepoCharter

Own the migration end to end. Measure the checkout first, resume from the first incomplete
state, and return one compact handoff. Do not make the operator shuttle prompts or reconstruct
provider state.

## Start or resume

Run the checkpoint from this skill before choosing an action:

```sh
python3 .agents/skills/migrate-repocharter/scripts/checkpoint.py --repo . --effective
```

If this skill is being run from the RepoCharter source tree rather than an installed consumer,
use its path under `kit/skills/migrate-repocharter/scripts/` instead. Treat the JSON-compatible
checkpoint as current truth; do not infer state from an earlier transcript.

Follow its route:

- `in-place`: continue in the exact checkout.
- `standalone-clone`: create an independent normal clone with the mode named by the checkpoint:

  ```sh
  python3 .agents/skills/migrate-repocharter/scripts/prepare_standalone.py \
    --repo . --dest <private-absolute-path> --mode <checkpoint-mode>
  ```

  `clean-head` leaves unrelated staged, unstaged, and untracked work only in the untouched source
  and gives `apply` a clean base. `preserve-state` is reserved for reviewed, already-prepared
  migration changes when linked-worktree recovery must transfer them byte-for-byte. The agent, not
  the operator, reviews that distinction. Continue only in the resulting normal clone. Never use a
  hook-trust bypass, `--allow-dirty`, stash, reset, restore, or clean as recovery.

## Migration state machine

1. Read the repository's own `AGENTS.md`, current project status, plan, relevant decisions, and
   pitfalls. Inspect legacy hooks and policy before changing either.
2. On a clean starting tree, run the installed RepoCharter `apply`. If unrelated work exists, use
   the standalone helper in `clean-head` mode; do not stack migration plumbing over it.
3. Scaffold repository policy while legacy gates are still active. Fire every declared fence with
   `agentkit measure`; retire a legacy safety gate only after the replacement is proven no weaker.
4. Reach the mechanical fixed point: `apply --dry-run` reports zero changes, strict verification
   passes, measurement passes, and every validation command declared by the repository passes.
5. Use the checkpoint's provider states. Promote only a provider whose `promotionRequired` is
   true. A provider marked current has matching adapter bytes, CLI version, checkout-local
   attestation, effective settings, and—under Codex—the exact trusted project hook source.
6. After any provider promotion, run one final fixed-point, strict/effective verification,
   measurement, and repository validation pass. Commit only the migration branch unless the user
   separately authorized integration.

Do not update durable status to say committed, pushed, merged, deployed, or healthy until that
event has succeeded. A branch switch or documentation-only commit in the same checkout does not
invalidate byte-bound provider evidence; rerun the checkpoint instead of reflexively promoting.

## Provider promotion

Codex promotion is valid only when the checkpoint reports the exact target
`<checkout>/.codex/hooks.json`. If Codex reports zero hooks in a linked worktree or reports a hook
from the primary checkout, review that every uncommitted path belongs to the prepared migration and
use the `preserve-state` standalone route. Do not repeat trust restarts after the exact
persisted-trust test has reproduced the defect.

When promotion is actually required:

- Codex: start a fresh trusted session in the target checkout, review the current definitions in
  `/hooks`, then run `agentkit self-test --repo . --promote-codex`.
- Claude Code: start a fresh session in the target checkout, establish workspace trust, then run
  `agentkit self-test --repo . --promote-claude`.

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
