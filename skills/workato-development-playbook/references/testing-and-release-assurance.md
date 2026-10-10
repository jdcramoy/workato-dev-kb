# Testing and release assurance

Read this reference for whole-graph audits, aggressive masking, automated
acceptance, human UAT, pressure and adversarial testing, repair, Jira evidence,
and Production smoke tests.

## Release contract

Before testing, capture:

- Development environment, project/folder, asset names, IDs, versions, running
  state, dependencies, connections, and rollback state;
- the business behavior and public contract being accepted;
- safe fixture, approved recipient or endpoint, and maximum external effect;
- expected Workato job, child work, state transition, and downstream result;
- irreversible, destructive, Production, or judgment-dependent stop conditions.

The contract prevents a repair from becoming an unreviewed redesign. Update it
only when a material decision is explicit.

## Whole-graph audit matrix

For every changed recipe, function, connector, Genie skill, MCP tool, App, or
hub, inspect the live persisted graph and answer these questions:

| Area | Required proof |
| --- | --- |
| Identity | Environment, project/folder, asset ID, version, owning job, and running state agree. |
| Trigger and contract | Input schema, optionality, defaults, limits, caller identity, and side-effect semantics match the public contract. |
| Provider calls | Connection, operation, inputs, output use, retries, rate limits, and error boundary are correct. |
| Mappings and formulas | No unresolved datapills, invalid formulas, sample-value literals, silent coercion, or stale fields. |
| Branches and loops | Normal, empty, partial, repeated, capped, and terminal paths are reachable and bounded. |
| Errors | Provider and business failures produce truthful structured outcomes; caught errors remain observable. |
| Children and functions | Inputs, outputs, identity, async behavior, failure propagation, and ownership are explicit. |
| State and idempotency | Claims, tables, watermarks, deduplication, retries, and ambiguous writes cannot duplicate or lose work. |
| Pagination and volume | Upstream filters, cursor or terminal condition, page accumulation, total/truncation semantics, and safety cap are correct. |
| Security | VUA, caller scope, privileged connections, private fields, prompt-like input, and cross-user continuation are isolated. |
| Masking | Every sensitive normal and error-path job line is masked at runtime. |
| Telemetry | Correlation, outcome, error fingerprint, duration, and environment are useful, sanitized, asynchronous, and fail-open where required. |
| Side effects | Recipient, destination, schedule, backlog, provider mutation, and external-effect cap are controlled. |
| User surface | MCP, Genie, App, message, document, or provider result is correct in the surface the user actually sees. |

For a batch, reconcile counts for targeted, changed, unchanged, failed, tested,
and packaged assets. One repaired sample never proves the batch complete.

## Masking and evidence hygiene

Mask aggressively whenever a line can contain employee or customer data,
compensation, payroll, time off, payment or routing details, email or chat
content, documents, credentials, OAuth material, provider tokens, signed URLs,
raw provider errors, or other sensitive business records.

Apply masking to triggers, provider actions, code, variables or lists when they
surface in traces, function and child calls, error monitors, telemetry, and
return steps. Apply the same standard to test harnesses.

Verify masking behaviorally:

1. place synthetic canaries only in fields needed for the fixture;
2. execute a normal path and a forced error path;
3. inspect the complete Workato job tree, including children and error monitors;
4. search every visible line for each canary and sensitive field;
5. confirm the evidence package contains only links, IDs, counts, hashes, schema
   assertions, or sanitized summaries;
6. fail the gate, repair, and rerun if any value appears.

Do not paste raw payloads into Jira, project memory, terminals, screenshots, or
chat as proof. If a governed exception is unavoidable, name the operational
need, owner, audience, retention boundary, and removal plan.

## Test ladder

### Structural gate

Re-read the saved asset and inspect the live canvas where needed. Reject stale
versions, unresolved mapping or formula errors, missing connections, broken VUA,
unmasked steps, provider calls outside error boundaries, or running-state drift
before UAT begins.

### Automated technical acceptance

Cover the normal path, empty and missing optional inputs, boundary sizes,
validation failures, provider failures, no-match results, partial results,
pagination, caught errors, child work, retries, idempotent replay, and complete
multi-tool or multi-recipe workflows. Use independent or surprise fixtures so
the suite does not merely validate its own assumptions.

Inspect the exact Workato job and downstream state. Count provider calls, retries,
children, records, claims, and external effects. A green parent job is not enough.

### Human UAT

Use the real user surface and an approved disposable fixture. Test wording,
conversation quality, visible identity, authorization, destination, formatting,
and read or write behavior. Record Pass, Partial, or Fail. The agent assembles
evidence but does not accept on the user's behalf.

Before an MCP server launch, a teammate tests it from their own MCP client and
reports the result. The agent can test the available caller and verify VUA
configuration, but that does not prove another caller's access. Give the
teammate concrete allowed and denied cases, capture their evidence, and label
any remaining identity coverage honestly. Do not demand that the agent
obtain or operate a second test identity.

### Regression

Rerun critical paths, previous defects, adjacent branches, unchanged functions,
legacy contracts that remain supported, and complete conversations. For MCP
inventory changes, reconnect a fresh client, compare exact names and count, and
call one safe read tool.

### Pressure

Warm the path, repeat like-for-like fixtures, and raise concurrency only within
an approved bound. Report run count, concurrency, completeness, errors, p50 and
p95, client wall-clock time, Workato job and queue timing, rate limits, response
size, and recovery. Check queue evidence before calling a timeout saturation.
Treat results as measurements of that fixture and environment, not as a capacity
certification or Production SLA.

### Adversarial

Exercise malformed, oversized, empty, duplicate, inaccessible, Unicode,
null-byte, prompt-like, stale-cursor, forged-continuation, changed-payload replay,
partial-provider, timeout, and ambiguous-mutation cases. Require correct behavior
or a truthful structured error with no unauthorized provider call or side effect.

### Red-team-style security checks

Attack the trust boundary with wrong-user and wrong-workspace requests, identity
substitution, privilege leakage, private-resource disclosure, policy bypass,
cross-user continuation reuse, missing masking, and unauthorized delivery. This
is an internal release control, not a substitute for an independent security
assessment when one is required.

Run identity cases through authorized accounts that are actually available. If
the current session has only one identity, complete structural and single-user
checks, then give cross-user cases to the teammate's launch UAT. Do not mark
those cases passed until the second caller has exercised them.

## Repair protocol

For each defect:

1. preserve the failing fixture, exact owning job URL, asset version, and safe
   error fingerprint;
2. classify the responsible layer: contract, recipe, connector, mapping,
   identity, configuration, provider, runtime limit, or client cache;
3. distinguish a platform or provider limitation from a defect we own;
4. change one bounded logical unit and preserve rollback;
5. re-read persisted state through the authoritative surface;
6. rerun the failed case, nearby regressions, masking checks, and the full gate
   appropriate to the risk;
7. record the defect, repair, and new evidence.

Never make the suite green by weakening the requirement, hard-coding the fixture,
hiding an exception, bypassing identity or policy, skipping the downstream check,
or silently reducing supported scope. Never replay an ambiguous mutation before
checking the destination.

## Evidence package

The implementation Jira item should contain:

- exact Development asset name, URL, ID, persisted version, and running state;
- sanitized fixture, expected result, actual result, and Pass, Partial, or Fail;
- owning Workato job URL and relevant branch, provider-call, retry, and handled
  error observations;
- downstream proof such as a child job, claim/table state, MCP response, provider
  read-back, delivered result, or confirmed absence of a write;
- defect, owning layer, repair, regression result, and remaining limitation.

The release record should add the final test matrix, pressure measurements,
risks and owners, rollback point, and Workato Recipe Lifecycle Management
manifest URL and ID. Follow the Jira project's actual issue-type convention;
the phrase "deployment ticket" does not itself select a Jira issue type.

## Production smoke and observation

Production action requires explicit authority. Before the test, verify imported
asset placement and identity, connection mappings, VUA or service identity,
properties, schemas, masking, running state, MCP discovery, backlog, lookback,
queues, claims, and deduplication.

Keep the smoke test deliberately small: one known fixture, one approved recipient
or endpoint if a write is required, no broad schedule activation, and a fixed
cap. Pass only when the Production job, children or state, user-facing result,
and masked trace agree.

Observe the first scheduled or real executions for new error fingerprints,
caught failures, latency, rate limits, identity drift, and masking regressions.
If a release invariant fails, stop or remove the affected exposure when safe,
preserve evidence, and restore the last approved package.

## Stop conditions

Pause automation and require a human decision before:

- Production mutation or promotion not already authorized at action time;
- destructive cleanup, irreversible write, broad backfill, or schema change to
  populated data;
- an external side effect outside the approved fixture, recipient, or cap;
- acceptance of a security, privacy, identity, data-loss, or policy exception;
- a product decision that changes the public contract or intended architecture;
- live credentials, secrets, or sensitive data must be exposed to proceed.

Do not treat a repairable defect, a failed first approach, or a green parent job
with an unproven downstream result as a stop condition.
