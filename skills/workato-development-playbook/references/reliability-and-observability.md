# Reliability and observability patterns

## Low-latency tool telemetry

Keep telemetry off the caller's critical path. Prefer an asynchronous function,
RecipeOps event, or equivalent fire-and-forget collector, fail open when the
collector is unavailable, and benchmark the user-visible path before and after
instrumentation. A material latency regression is a failed observability change.

Use a unique source job/event ID for idempotency. Populate caller identity,
tool/action, generation/version, calling recipe and job, environment from an
environment-specific project property, start/end or duration, outcome, and a
bounded output-size or token estimate from runtime context. Never hardcode those
values when Workato exposes authoritative datapills, and avoid secrets or raw
business payloads unless governance and the use case explicitly require them.

If a scheduled reconciler or backfill shares the same table, it must preserve
authoritative dimensions already written by the real-time path. Do not overwrite
tool generation, environment, caller, or identity with generic fallbacks. Derive
monitored membership from authoritative server/project relationships where
possible rather than maintaining unexplained recipe-ID arrays.

## Durable claims for asynchronous workflows

For notifications, enrollment, provisioning, and other externally visible
actions, make the claim/dedup identity represent the actual business event and
delivery occurrence. Acquire a durable claim before queuing an asynchronous
send. Use a unique key or an equivalent atomic lookup-and-create sequence so
retries and separate scheduled runs cannot re-send the same event.

An ordinary replay is suppressed by the existence of the deterministic claim,
not only by a terminal `SENT` status. Filtering the lookup to sent rows makes a
reserved `CLAIMED`/`SENDING` row invisible and permits a second dispatcher to
send the same event. Model an intentional retry as a distinct bounded attempt
against the existing claim instead of pretending no claim exists.

Model state deliberately (for example `CLAIMED`, `SENT`, `SUPPRESSED`,
`FAILED_RETRYABLE`) rather than treating any non-error parent job as delivered.
When a retry is intentionally a new delivery attempt, give it a distinct,
bounded occurrence identity; when it is a replay, suppress it.

## Handled errors are still errors

Recipes that catch errors can report `succeeded` while the requested work did
not happen. Emit sanitized structured telemetry for handled exceptions: source
recipe and job, stage, fingerprint/class, whether the business action completed,
and downstream disposition. Avoid raw payloads, secrets, and unnecessary user
data.

Keep outcome semantics explicit. Separate active states, terminal success,
terminal failure, partial success, provider no-match, warning, unknown, and
recovery. Use a positive allowlist for "stuck" or otherwise actionable states;
negative terminal exclusions silently become wrong when a new status appears.
A provider no-match or blank/unknown health value is telemetry unless the
business contract makes it a failure.

Alert new material fingerprints at an operational severity with a stable
incident key and cooldown. Deduplicate repeated failures, but never hide a new
class merely because a different class dominates a rate threshold. If a flow can
fail open, add a bounded job-trace backstop that scans successful jobs for catch
lines with real error objects and persists a primary-keyed observation.

Set time-based alerts against the real operating cadence. A pending item that is
waiting for the next governed cleanup/reconciliation sweep is not yet a failure;
alert immediate terminal failure states, abandoned in-progress claims after a
short timeout, and true SLA breaches only after the normal interval plus grace.
Reconcile open incidents against authoritative recovery evidence so resolved
conditions do not remain permanently open.

Operational notifications should identify the affected asset by a human-readable
linked name, include the exact owning job link when available, and link the actor
through an approved email or messaging identity. Render real line breaks and
verify the delivered message in the destination. Escaped `\n`, a recipe URL with
no name, or a blank job label is not an acceptable preview-only success.

## Event-driven versus scheduled remediation

Prefer an event-driven Workato/RecipeOps signal where it supplies the precise
failure and identity needed for immediate action. A periodic scanner is useful
for audit, drift detection, and evidence collection, but should remain
read-only until it can prove the exact target and ownership relationship.

For any destructive recovery (connection reset, record deletion, entitlement
change), fail closed: require the canonical error signature, authoritative
caller identity, exact parent/child relationship, scope ownership, and a single
unambiguous target. No-match or multi-match must record evidence and do nothing.
Do not infer a destructive association merely because records share a user,
time window, project, or connector family.

## Bounded data processing and SQL Collection

Treat connector and UI limits, especially 1,000-row table/search/selectors, as
page bounds, not evidence that the dataset ends there. Push simple filters and
date/watermark predicates into the source query, page with continuation tokens
or a documented full-page/partial-page terminal condition, and add a generous
explicit safety cap that emits a visible failure instead of silently truncating.
Filtering after a fixed number of raw pages is especially dangerous because
eligible rows beyond the raw boundary are never evaluated.

Do not use a static number of pages as a test fixture or production stopping
rule. When the provider exposes a cursor, drive a repeat/while loop to its
documented terminal condition. When it exposes no cursor, do not fabricate
provider pagination; if a bounded local view is useful, make the offset/limit,
authoritative total, and truncation semantics explicit.

Use one job-scoped SQL Collection as the accumulator across repeat iterations.
Insert each page/recipe result into that same collection, normalize connector
field names at the SQL boundary, then perform compound predicates,
deduplication, joins, and compact anomaly shaping there. Verify the collection
contains multiple iterations; a loop-local replacement can make only the final
iteration visible.

Batch independent high-volume Data Table creates/upserts and guard the action
when the batch is empty. Keep single-record writes when they are transactional:
claims, progress and state transitions, reconciliation, cleanup claims,
tombstones, or one-event observers. Advance watermarks only after every page and
side effect succeeds.

## Materialized search and generation refreshes

For a refresh that feeds search, routing, or eligibility decisions, row count
and a green parent job are not sufficient acceptance evidence. Verify a known
record and an aggregate non-null/type check for every search-critical field in
the candidate active generation, then prove a representative downstream query.

When a read model spans canonical rows plus derived search, relationship, or
other indexes, build every table under one unpublished version. Validate the
complete set together, including expected and unique counts, required non-empty
sets, referential integrity, and domain-appropriate cycle checks, before changing
one shared current-version pointer. Readers resolve that pointer once per
request and filter every table read to the resolved version; they must never mix
current and previous tables or describe a partial version as complete.

When a sweeper coexists with generation builds, retain the last known-good
generation until the replacement is authoritatively complete. Cleanup must be
gated by durable state that agrees on the active/protected generation; an
unknown or building state is fail-closed for destructive cleanup.
Retain the previous complete canonical-and-index version as one rollback unit,
guard empty cleanup batches, and clean obsolete versions only after promotion.
Do not advance the pointer when any build, validation, or required write fails;
prove success from the pointer and every required table rather than from the
refresh recipe's parent-job status alone.

## Dynamic operational scope

For promoted monitoring, consume affected recipe/job IDs from authoritative
RecipeOps events and exact-match them against an environment-local registry.
Populate that registry from in-scope recipe-start events and periodically
reconcile it. Use canonical project/folder paths or authoritative dependency
relationships for membership; do not carry Development IDs into Production.

Connection monitoring should discover connections used by registered in-scope
recipes. Do not enumerate an entire workspace merely because a connection
picker is capped or lacks project metadata. When a real-time trigger cannot
resolve project membership, use a hybrid: a small verified real-time dependency
set for immediate coverage plus a scheduled recipe-scoped discovery/audit for
durability.

Incident links must come from one authoritative source tuple. Use the affected
recipe ID and its actual job ID together, validate the direct URL, and leave the
job link unavailable when ownership cannot be proven. Never combine the
monitoring recipe's ID with the affected recipe's job handle.
