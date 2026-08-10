---
name: project-memory
description: Read and update this repository's durable project state — SPEC, PLAN, STATUS, decision records, and pitfalls. Use at session start when resuming work, before choosing an approach in an area where a previous attempt failed, and at checkpoint or handoff time when something has been learned or decided.
---

# Project memory

The durable state of this repository lives in `docs/project/`, in plain markdown at plain
paths. No harness feature is involved, which is why it survives harness changes and why it
is the one tier that is genuinely shared rather than reached through an adapter.

This skill is the read and write protocol for that state. Its body loads only when
triggered, so the cost of having it is a description line.

## What owns what

| File | Owns | Discipline |
|---|---|---|
| `SPEC.md` | Approved goals, locked constraints, non-goals, definition of done | Changing a locked section needs the operator's explicit approval |
| `PLAN.md` | Active milestones, sequence, acceptance gates | Completed plans are **deleted**, never archived in place |
| `STATUS.md` | Current verified state, blockers, next action | **Rewritten** as a snapshot, never appended to |
| `decisions/NNNN-slug.md` | One decision each: context, choice, consequences | **Immutable.** A reversal is a NEW record that supersedes |
| `pitfalls.md` | Verified negative results only | Capped and rewritten. See below |
| `obligations.md` | Live debts to and from an external counterparty | Optional; only where one exists |

## Reading

Read by trigger, never by default. Reading all of it at session start is the habit this
layout exists to break — the corpus costs nothing while it sits on disk and a great deal
once it is in context.

- Resuming work → `STATUS.md` first. It carries the next action.
- Changing scope or promising something → `SPEC.md`.
- Starting a task → `PLAN.md` for the acceptance gate.
- Reversing an architectural choice → the relevant decision record, before arguing with it.
- **Choosing an approach in an area where something failed before → `pitfalls.md`.**

## Writing

Three rules, and the third is the one that gets broken.

1. **One canonical home per fact.** If it belongs in SPEC, it does not also go in STATUS.
   Restatement across files is how contradictions are born and go unnoticed.
2. **Rewrite, do not append.** STATUS and pitfalls are snapshots. If either is growing
   monotonically, it is being appended to, and that is the failure mode.
3. **Delete on completion.** A finished plan is removed from PLAN.md in the same commit
   that finishes the work. Git history is the archive.

## Pitfalls: what it is for, and its cap

`pitfalls.md` holds verified negative results — *we tried X, it failed for reason Y, here
is the observable that proves it*. Decision records cover decisions; this covers the
failures that never became decisions, which is the class that otherwise has nowhere to go
and quietly regrows a much larger memory system around itself.

**Hard cap: 100 lines / 8 KB**, enforced by `agentkit verify`. The cap is the mechanism,
not a formality. When it is reached, evict in this order:

1. A pitfall whose failure is **now caught by a mechanical gate** — delete it and cite the
   gate in the commit message. The gate is the knowledge; keeping both is restatement.
   This should be the most common eviction.
2. A pitfall describing code that no longer exists.
3. Still at cap: the oldest `last-confirmed`, noted in the commit body so a re-encounter
   reads as a regression rather than a novelty.

Entry format:

```
### <short name>
- tried: <what was attempted>
- happened: <what actually went wrong>
- observable: <the error, measurement, or failing command that proves it>
- last-confirmed: YYYY-MM-DD
```

## When to write

At **checkpoint** and at **handoff** — the two moments where both the failure and the
attention to record it exist at once. Writing a pitfall the moment it happens is the only
time it is cheap; a week later nobody remembers the observable.

RepoCharter disables Claude auto memory by default because this project layout already gives
durable knowledge a reviewed, portable home. A repository may explicitly opt in with a recorded
reason, but that machine-local scratchpad is neither shared nor code-reviewed. Nothing
authoritative may live there; if a fact matters to the next person, it goes in `docs/project/`.
