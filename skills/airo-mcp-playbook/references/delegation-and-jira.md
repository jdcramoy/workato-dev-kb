# Delegation gates and Jira update policy

Imported from the Codex `efficient-airo-delivery` skill. These are process rules for AIRO/Workato delivery work, not tool-call mechanics.

## Cost-aware delegation

- Always fan out independent bounded work when it reduces cost or elapsed time.
- Delegate mechanical and read-only tasks to the least-expensive capable model:
  file inventory, targeted searches, schema extraction, job-log summarization,
  latency/count tabulation, test drafting, and evidence comparison.
- Give each delegate an exact question, narrow artifact scope, concise output
  expectation, and no mutation authority.
- Reserve frontier/high-reasoning work for architecture, ambiguous diagnosis,
  security and access decisions, mutation review, and acceptance.
- Do not delegate duplicate investigations or open-ended exploration.

## Plan and execution gates

- Every phase plan must state objective, non-goals, evidence baseline, assets,
  ordered changes, model allocation, rollback, validation, success metrics, and
  stop conditions.
- Measure the bottleneck before changing architecture. Prefer the smallest
  reversible change that removes the measured constraint.
- Preserve snapshot atomicity, current-version isolation, rollback retention,
  field completeness, and the no-request-time-Workday boundary.
- Re-read deployed state immediately before mutation.
- Run targeted comparison first, then the full regression/security/pressure
  suite only after the targeted gate passes.

## Jira update policy

- Do not update Jira for exploration, intermediate hypotheses, each subtask, or
  routine progress.
- Update only at a decision-quality milestone: approved plan, verified baseline,
  implemented-and-validated phase, material blocker, or release handoff.
- Accumulate evidence in memory/local files and publish one concise comment with
  verified facts, metrics, affected assets, rollback state, and next decision.
