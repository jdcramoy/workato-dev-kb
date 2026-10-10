---
name: workato-development-playbook
description: >-
  Inventory, design, build, audit, test, repair, document, and prepare Workato
  systems for governed release across environments. Use for Workato recipes,
  functions, connectors, hubs, data-table workflows, Genies, MCP integrations,
  Workflow apps, lifecycle manifests, Production smoke tests, and operational monitoring;
  do not use for connector-specific business rules.
---

# Workato Development Playbook

Use this as the cross-project operating model for Workato delivery. It is based
on repeated delivery across Workday, Seismic, Gmail, Slack, Genies, MCP servers,
scheduled hubs, and operational monitoring. It complements `airo-mcp-playbook`,
which carries AIRO-specific failure modes, and `workato-skill-builder`, which
carries recipe, Genie, and MCP construction mechanics (available in the
workato-agentic-kit). Keep provider-specific authorization, data semantics, and
business policy in the relevant connector or project skill. During planning,
design, build, and test, ground platform claims in the `workato-kb` skill's
local corpus and cite the docs URL on every claim.

Predict-then-check is mandatory on pushes and lifecycle ops: state the exact
observable signal BEFORE the mutation, then close the prediction against the
observed outcome (matched/deviated/failed). Deviations surface immediately for
attention — a surprise is never wasted.

## Actor model

Keep human authority, model coordination, and execution surfaces distinct:

- The human operator owns the requirements, approves the plan, sets scope and
  side-effect limits, steers the work, performs final UAT, and authorizes
  Production actions or exceptions.
- A Codex or Claude session keeps the working context and coordinates tool calls,
  testing, evidence, and bounded repairs within that human-directed scope. Do not
  describe it as an independent or autonomous external system.
- AIRO MCP is the Workato development surface the model calls for asset
  construction, configuration, validation, test cases, and targeted reads.
- The Workato Dependency Tracker MCP (`workato-dependency-tracker`) answers
  cross-asset dependency questions AIRO does not expose: which recipes touch a
  given connection, object, or field (blast radius), recipe lookup by name with
  job stats and running state, connection inventory and authorization health,
  and full recipe logic by recipe ID + workspace ID. Its reads are advisory —
  verify material state through AIRO or the live asset before mutating.
- The Workato Developer CLI supplies supported lifecycle operations that AIRO
  MCP does not expose reliably. The `wt` wrapper (`~/.local/bin/wt`, same
  stored credentials as the Developer CLI) supplies Developer-API operations
  neither exposes: export manifests + package export/import for environment
  promotion, recipe delete/copy/force-run/versions/job-counts, job
  repeat/cancel, connections CRUD, lookup-table row writes, project-property
  upserts, folder/project CRUD, and data-table record queries
  (`wt dt query` — the only external path to data-table rows; AIRO's
  `data_table_query` can 404 in some workspaces).
  `wt api METHOD /api/...` is the raw passthrough.
- Signed-in browser automation handles genuine UI-only actions and visual
  verification. The user-facing client or destination proves the final outcome.
- The Workato AIRO product interface is a separate actor. Do not describe the
  model's cross-tool behavior as a current AIRO product capability unless
  that behavior is actually being executed by the product.

## Non-negotiable outcome

Do not equate a saved asset, a green parent job, or a valid response schema with
working software. Accept the change only when the authoritative Workato state,
the owning job and child work, and the downstream business result agree.

Carry authorized Development work through inventory, implementation, whole-graph
audit, automated acceptance, repair, regression, evidence, and promotion-ready
handoff. Do not stop after one representative recipe, the first green test, or a
repairable defect.

## Execution surfaces

- Use AIRO MCP for semantic recipe and connector construction, pull/edit/push,
  mappings, Workato configuration, connector validation, test cases, and targeted
  reads. After any mutation, independently read back the persisted Workato asset
  and verify identity, version, and intended state.
- Use the Workato Developer CLI for supported workspace and recipe lifecycle
  operations that AIRO MCP does not expose reliably, such as exact stop/start
  actions. Use `wt` for the Developer-API-only surface: `wt dt` (data-table
  row queries), `wt manifests`/`wt packages` (promotion packages),
  `wt recipes`/`wt jobs` (delete, copy, force-run, versions, repeat, cancel),
  `wt connections`/`wt lookup`/`wt properties` (writes AIRO lacks).
  Do not treat them as a second connector-development path.
- Use the signed-in Workato browser only for genuinely UI-only configuration,
  visual inspection, test-panel execution, job-trace review, connection or VUA
  verification, or an explicitly authorized Production hotfix.
- Use the user-facing client or destination for final behavior. An MCP tool must
  work from a fresh client as the calling user. A Genie must work in its actual
  conversation surface. A provider write must be read back from the destination.

Choose the most authoritative surface for each action. Verify material changes
through a second surface when silent failure, stale metadata, or cached discovery
is possible.

## Delivery lifecycle

### 1. Orient and contract

Resolve the environment, project/folder, live asset IDs, current version,
dependencies, connections, running state, and job ownership as one identity
tuple. Treat a supplied URL or ID as input to verify, not as proof of environment
or authorization. For the dependency half of that tuple, prefer the Workato
Dependency Tracker MCP (`search_recipe_dependencies`, `list_connections`,
`get_recipe`) over folder-by-folder AIRO scans: it resolves the blast radius of
a connection/field/table, finds a recipe by name with its fail rate and running
state, and inventories broken or unauthenticated connections.

Write a narrow change contract: intended behavior and acceptance criteria; exact
assets and fields allowed to change; non-goals and protected interfaces; current
persisted state and rollback point; safe fixtures and side-effect cap; observable
success condition; and stop condition. For a repeated or multi-recipe change,
inventory the complete target set and capture each asset's version and running
state first.

**Prior-art sweep before designing anything new** (standing rule):
search the knowledge tree (INDEX.md + domain files), installed skills, existing
ledgers, and live recipes for an existing mechanism covering the need. If one
covers 60%+ of it, design as "extend it" with the delta named — never a parallel
rebuild. State what was searched and what was found in the plan.

### 2. Build in Development

**Spec gate (standing rule):** before any build, a written spec (goal, scope,
verifiable acceptance criteria, design gate, risks) — or the requirements-clarity
bypass when requirements are already crisp. After any build, cite a scored
review of the delivered work against that spec in the report.

Read the live asset immediately before mutation. Apply one bounded logical
change, push it through the supported surface, then re-read the persisted asset.
If a recipe must stop for a push, start a fresh AIRO MCP builder session after the
stop so stale lifecycle or action metadata does not cross the boundary.

Preserve the requested product architecture. Do not hard-code one fixture,
bypass the intended identity or policy boundary, suppress required dynamic
behavior, or narrow the public contract to make a test pass. A temporary
containment must be explicit, bounded, non-default, reversible, and incapable of
becoming the de facto product path.

For connectors, the model uses AIRO MCP for source construction,
validation, and persisted-source verification. Confirm the stable connector
identity, saved source hash or an equivalent persisted comparison, and dependent
recipe bindings before calling the change ready.

### 3. Audit the complete live graph

Audit every changed asset before live UAT. Cover the trigger or public contract,
provider actions, mappings and formulas, conditional and repeat branches, error
boundaries, return steps, child/function calls, connections and VUA, masking,
retry semantics, pagination and caps, concurrency, schedules, tables and durable
claims, telemetry, and downstream side effects.

Use visual inspection when an export or API can hide a broken formula, unresolved
datapill, implicit connection, or stale UI-only setting. Each changed MCP tool
must have no unresolved mapping, formula, or datapill errors, and every provider
call must sit inside the intended structured error boundary.

### 4. Run the test and repair loop

Start this phase after the reviewed plan has been built and the changed assets
have been read back from Workato. Testing may send a defect back into a bounded
build-and-verify cycle.

Run automated acceptance and human UAT for each affected workflow. For Genies,
MCP servers, and changes affecting identity, sensitive data, or volume, extend
the Development ladder through bounded pressure, adversarial, and red-team
checks. Scope the fixtures and intensity to risk:

1. structural and schema gate;
2. automated technical acceptance for normal, empty, boundary, provider failure,
   partial-result, pagination, idempotency, and multi-step workflow cases;
3. human UAT through the real user surface with approved disposable fixtures;
   before an MCP server launch, a teammate also tests it from their own client;
4. regression for critical paths, previous defects, unchanged functions, and
   supported compatibility contracts;
5. bounded pressure tests with warmups, repeats, p50/p95, errors, Workato and
   client timing, queue evidence, completeness, and recovery;
6. adversarial input, identity, authorization, continuation, idempotency, and
   ambiguous-mutation cases;
7. red-team-style checks against trust boundaries, privileged connection
   isolation, private-resource disclosure, policy bypass, and missing masking;
8. fresh-client rediscovery and one safe read after MCP inventory changes.

Test cross-user behavior with authorized identities when available. If this
session cannot exercise a second identity, verify VUA configuration and the
available caller's behavior, record the coverage limit, and hand the exact
remaining cases to the teammate before launch. Do not manufacture a second
account or claim that single-user testing proves cross-user access.

When a case fails, reproduce it with a bounded fixture, find the exact owning
job, and identify the failing layer: contract, recipe, connector, mapping,
identity, configuration, provider, or client cache. Repair that layer, re-read
the persisted state, rerun the failed case, run nearby regressions, and repeat
the release gate. Do not retry an ambiguous write until the destination proves
whether it committed.

Read [testing and release assurance](references/testing-and-release-assurance.md)
for the full audit matrix, masking gate, repair protocol, test ladder, metrics,
evidence package, and stop conditions.

### 5. Capture evidence and prepare release

Record exact Development asset URLs and IDs, persisted versions, running state,
sanitized fixtures, expected and actual results, owning Workato job URLs,
relevant branches and provider-call counts, child or table outcomes, defects and
repairs, regressions, limitations, and Pass, Partial, or Fail status.

Update Jira only at decision-quality milestones. Keep raw PII, credentials,
message bodies, documents, signed URLs, and provider payloads out of Jira and
project memory. Use links, counts, hashes, canaries, schemas, and sanitized
summaries instead.

Build a Recipe Lifecycle Management manifest only after UAT. Include every
changed asset and required dependency, and no harness, stale prototype, secret,
test data, archived asset, or unchanged folder peer unless deliberately required.
Production promotion remains a separate governed human action.

## Aggressive masking is a release gate

Treat Workato job history as a sensitive data store. Mask every data-bearing
trigger, provider action, code step, child call, error handler, telemetry call,
and return step that can contain PII, credentials, business content, provider
tokens, or signed links. Test harnesses and Development runs are not exempt.

A configured masking control is not proof. Execute normal and error paths,
inspect the actual job tree, and verify every relevant line is masked. Prefer
synthetic canaries, hashes, counts, schemas, or equality assertions over exposing
the record. Any sensitive value in a trace, copied log, Jira comment, or saved
evidence fails the release gate and requires repair plus a clean rerun.

## Classify every failure branch

Every error branch in a recipe is classified by failure type and handled
differently. One undifferentiated handler that retries everything turns
permanent failures into retry storms and capacity failures into incidents.

- **TRANSIENT** — retry with backoff: network blips, rate limits.
- **PERMANENT** — quarantine and notify once, never retry: bad data shape,
  a deleted referenced record, a disabled connection.
- **CAPACITY** — back off and reschedule: quota, throttle.

This mirrors the deliberate per-component failure policy in Workato's otto
platform codebase: its safety judges fail open on rate limits and fail
closed otherwise, and its event pipeline quarantines permanently-dead
sources instead of retrying them — "quarantine over retry: a handled
outcome never pages anyone."

## The two-signal rule

The user-facing notification and the operator-facing monitoring signal are
separate paths with separate truth. What the business user sees ("retry
scheduled, we'll notify you") may legitimately differ from the operator
signal (a real error verdict recorded for monitoring). Every run terminates
in exactly one authoritative outcome — a latch, never two contradictory
statuses.

Workato form: notification recipes do not double as monitoring, and
monitoring reads the run outcome, not the Slack message. Derived from
Workato's otto platform, which broadcasts a soft success to the user on a
529 while internally recording the true API_ERROR verdict — one is what the
user sees, the other is what an operator sees.

## Execution discipline

- For an existing scheduled Development recipe, stop it, use **Test Recipe**,
  inspect the resulting job and downstream state, and restore the intended
  running state. Do not change its recurring cadence merely to induce a run.
- Ask the user only for a material product decision, missing authority, access,
  or a skill gap that cannot be resolved safely.
- Start from supplied IDs and URLs, make narrow reads, reuse handles, and keep a
  resumable change and evidence ledger. Efficiency never excuses missing
  validation or incomplete output.
- Reconcile a batch by targeted, changed, unchanged, failed, tested, and packaged
  counts so partial completion cannot masquerade as success.

## Portability and scope

- Portable recipes discover their live context or use legitimate environment
  mappings. Never embed environment-specific connection, recipe, folder,
  project, or runtime-session IDs in portable logic.
- Use trigger/job context datapills for the calling recipe ID, job ID, caller,
  workspace, and other runtime identity. Verify them in a live job because an
  exported DSL can display a resolved sample value that looks hardcoded.
- Use an environment-local registry populated from authoritative runtime events
  or project relationships when the monitored asset set must follow promotion.
  Do not replace a hardcoded list with a comma-delimited property or a
  workspace-wide scan and call it dynamic.
- Use project properties only for runtime configuration that genuinely varies by
  environment. Do not use them to make design-time Workato association fields
  dynamic; those associations often require a literal picklist value.
- Keep ordinary test routing at the ingress/recipient-selection layer. Do not
  modify shared downstream dispatchers, messengers, or production infrastructure
  merely to run a controlled test.
- Keep test harnesses stopped outside active UAT. After evidence is captured,
  retain them in an archive project with a clear description unless deletion is
  explicitly requested.

## Required evidence

- Record exact asset URLs, versions, test job URLs, and the relevant persisted
  output/claim/delivery outcome. Verify every job URL resolves under its actual
  owning recipe; do not synthesize links from unrelated recipe and job fields.
- Report what is proven, what is structural only, and any remaining platform
  limitation. Never turn a successful setup action, a green parent job, or a
  plausible explanation into proof.
- Update durable project memory at a decision-quality milestone with concise
  deltas: request, implementation, evidence, rollback state, and blocker.
- For a batch, reconcile the target inventory against changed, unchanged,
  failed, tested, and packaged assets. Every asset actually changed belongs in
  the release manifest; no unchanged asset belongs merely because it shares a
  folder.

## Production hotfix boundary

Use a Production-first repair only when the user explicitly authorizes the
specific Production mutation and the incident warrants it. Scope it to one
small reversible change, verify it live, run a bounded smoke test, then mirror
the correction into Development. Do not use this route for broad refactors,
unbounded backfills, schema changes to populated tables, or destructive cleanup
without separate explicit scope.

After an authorized Production import, verify placement, versions, connections,
VUA or service identity, properties, schemas, masking, running state, and exact
MCP inventory. Use one known fixture, one approved write target where needed,
no broad schedule activation, and a fixed side-effect cap. Inspect backlog,
lookback windows, queues, and deduplication first. Pass only when the Production
job, downstream result, and masked job trace agree.

## Detailed references

- Read [testing and release assurance](references/testing-and-release-assurance.md)
  for the audit matrix, masking protocol, test ladder, repair loop, Jira evidence,
  Production smoke test, and stop conditions.
- Read [delivery and release controls](references/delivery-and-release.md) for
  test harnesses, lifecycle manifests, imports, and Production hotfixes.
- Read [reliability patterns](references/reliability-and-observability.md) for
  claims, retries, async delivery, fail-open telemetry, pagination, batching,
  SQL Collection, monitoring, and versioned read models.
- Read [Workflow app and MCP UI surfaces](references/app-and-mcp-surfaces.md)
  before changing a Workflow app, iframe resource, long-running operation UI, or
  clipboard and link behavior.
- Read [MCP server delivery](references/mcp-server-delivery.md) for tool
  contracts, VUA, inventory, rediscovery, compatibility, and benchmarking.

## Specialized recovery

When a Slack-backed Genie reports maintenance mode after a reconnect, treat a
stale Slack-to-Workato registration as a distinct failure mode. Preserve the
approved Slack app unless replacement is explicitly required. Rebind that same
app, update only the newly generated callback endpoints, preserve identity and
scopes, verify the URLs and credentials without persisting secrets, restart the
Genie, and run one inbound UAT. If the creation modal appears stuck, refresh the
Workato page and verify the actual trigger list before retrying.

## Routing

- Use `airo-mcp-playbook` for every AIRO MCP call.
- Use `workato-dependency-tracker` for dependency/blast-radius questions,
  connection inventory and authorization health, and recipe lookup by name —
  orient before touching assets; not a mutation surface. Live-verified
  gotchas: `get_recipe` returns the FULL raw recipe JSON and can
  exceed 200K chars for one recipe (~60K tokens) — prefer AIRO `recipe_get`
  for reading recipe logic and reserve tracker `get_recipe` for workspaces
  AIRO can't reach. `search_recipe_dependencies` returns one row per recipe
  step (denormalized, with empty rows for never-run steps) — dedup by
  recipe_id and read job stats from the rows where `running` matches the
  current state. `list_connections` with `authorization_status: "exception"`
  is org-wide across all workspaces with real root-cause errors — ideal for
  audit triage, and its breadth is broader than any single project's view.
- Use `workato-skill-builder` for recipe, connector, Genie, or MCP build, edit,
  test, or promotion-readiness work (available in the workato-agentic-kit).
- Use `workato-recipe-control` for CLI recipe start/stop where available
  (available in the workato-agentic-kit).
- Use `workato-browser-editor` for UI-only mutation or visual trace work
  (available in the workato-agentic-kit).
- Use `mcp-optimization-playbook` when an MCP change affects latency, payload,
  pagination, or model-context cost (available in the workato-agentic-kit).
