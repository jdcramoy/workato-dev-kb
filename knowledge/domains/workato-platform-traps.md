# Workato platform traps — recipe builder, APIs, data tables, testing

Every generalizable trap learned while building and operating Workato
recipes through platform MCP tooling, the Developer API, and browser
automation. Status sections and estate topology are deliberately absent;
each entry is the reusable lesson, not the incident log.

## Why does a push fail on a running recipe?

- Recipe-builder push on a RUNNING recipe fails. Stop first, push, then
  start — and verify: CLI start/stop can silently no-op on some platform
  recipes, so confirm the Running flag with a read-back (or the
  connections/edges changing) before trusting either tool.
- A "Running" recipe rejects edits with "Cannot edit recipe … because it
  is running". CLI stop can lag or no-op on custom-schedule recipes —
  retry the stop and verify the Running flag before re-pushing.
- The recipe editor's Edit button is disabled while the recipe is
  running. A "click Edit did nothing" is a running-recipe symptom, not
  an automation failure.

## Why does UI validation block a recipe the runtime accepts?

Editor start/test validation is STRICTER than runtime. It hard-blocks
on (a) dynamic foreach-item pills sourced from custom-code arrays and
(b) `records[0]` pills inside `ruby()` bindings — both resolve fine at
runtime. Fixes: flatten to static-pill flows (single-item output,
declared lists); bind the array and index inside the ruby expression.

## How do code snippets silently poison recipe data?

- `code_input.data` values set with the plain setter store the
  `ruby(…)` text as a LITERAL string. Always use the typed setter
  mode that declares the value is Ruby.
- An f-string (`"{...}"`) rendering of an ARRAY pill into code input
  yields Ruby inspect (`{"k"=>"v"}`), not JSON — a downstream
  JSON.parse fails with an unexpected-token error. Scalars are fine.
  Bind arrays with a `to_json` ruby binding and have the snippet
  accept both native Hash/Array and JSON strings.
- A blank value in a function-trigger test trace usually means the
  test modal was submitted empty, not a mapping bug.

## How do you push a recipe code change safely end to end?

1. Stop the recipe (verify the stop took).
2. Push.
3. Re-pull and fix every degradation in ONE pass.
4. Push once more, then test immediately with no further pushes.

Rationale: each builder push can degrade round-tripped steps (see the
next section), so the last push must be followed directly by the test.

## What does a builder push round-trip corrupt?

An AIRO-style builder push can rewrite payload-asset-type fields to
option titles, materialize filter placeholders, rewrite picklist VALUES
to TITLES, and null a connection `account_id` on adhoc steps. The
static check accepts it; the UI Test banner blocks. The safe workflow
is the push sequence in the section above. Repair patterns: re-set the
connection after every push, and re-pull before further edits.

## What are the data-table rules that prevent silent data loss?

- Data-table schemas are immutable: column evolution = a new
  versioned table + a migration recipe.
- Column-to-field-ID maps come ONLY from a schema read-back; builder
  schema views can mislabel or swap columns.
- An update call with a `columns` parameter regenerates EVERY
  business-column field ID and wipes populated tables — never use it.
  Field-ID-keyed parsing breaks on any schema edit; name keys survive.
- After a field-ID regeneration, recipes referencing old IDs keep
  silent dead mappings — rewire all table steps and re-set the table
  reference on each step to refresh the cached schema.
- Data-table row writes live on a DEDICATED host
  (`data-tables.workato.com`, path `/api/v1/tables/<table-uuid>/…`);
  the main API host manages schema only. The record body wraps fields
  in a `"document"` key: `{"document": {"<column_name or field_uuid>": value}}`.
  Everything else (record/values/data/fields/row wrappers, positional
  arrays) fails with a misleading parse error.
- Table identifiers are UUIDs on the records host; numeric workspace
  IDs 404 there. Picklist-style endpoints use numeric IDs and records
  endpoints use UUIDs — both work, never mix hosts in one call.
- The query API accepts a `sort` key but SILENTLY IGNORES it
  (insertion order always) — walk pages for newest-write facts.
- A `--where`-style equality filter can return 0 rows for every value
  even for visibly present ones in some client versions — page
  unfiltered with a continuation token when in doubt.
- AIRO-style `data_table_query` may 404 in a workspace (the AI query
  route is not deployed everywhere); list/get still work — verify
  before assuming the tool is broken estate-wide.
- Data-table creation can report a folder error yet succeed anyway
  (silent-success family) — verify with a read-back after creation.

## What Ruby subset do recipe formulas actually allow?

- No Digest, no `Time.now` — use the platform `now` value.
- No `#{}` interpolation — use string concatenation.
- `.length`, `.present?`, `.match?`, `pluck('key')`, `.join(', ')`
  and `.to_json` are allowlisted; `JSON.generate` is not.
- Bindings must be plain datapills (no operator expressions) —
  concatenate inside the expression.
- No bare `return` in invoked ruby code — final-expression value or
  an explicit `error()`.
- `lambda` is mangled by the platform and silently returns wrong
  values; top-level constant assignment breaks; array datapills
  arrive as Ruby-inspect text with raw Time objects — parse by
  scanning `{...}` blocks with per-field regexes, never JSON.parse.
- `"\n"` literals inside ruby() values collide with the DSL wrapper's
  quotes — encode newlines as escaped forms instead.
- Boolean guards: `blank?`/`present?` are WRONG for boolean checks —
  `false.blank?` is true, so a legitimate `false` nulls out. Only an
  explicit `== nil` comparison is safe. The formula validator rejects
  `.nil?` but accepts `== nil`.

## How do datapills and step ordering behave?

- Datapills flow forward only — a step cannot reference a later step.
- `declare_variable` output pill `['value']` only registers after the
  `type` is set: set name+type first, then value, or downstream steps
  get invalid-path errors.
- Logic steps (if/elsif/else/foreach/stop) must NOT carry
  connector/action params — passing them attaches config the step
  type rejects, and the recipe fails UI start with "some steps haven't
  been setup correctly". Remove and re-add the step without them.
- `add_step` position is advisory for blocks: a block may append at
  the END (unreachable after a return step, i.e. silent dead code) —
  always move the block to position and verify the rendered recipe
  before pushing.
- `set_foreach_source` has mangled a pill name in the field — always
  read the rendered DSL in the set response.
- Loop-item pills require OBJECT list items: a foreach over a
  string-list exposes only loop metadata, and the item pill is nil at
  runtime. Map to objects and reference named fields.
- `project_property['Name With Spaces']` inside a foreach ruby
  binding corrupts the emitted DSL — route the property through a
  Ruby snippet step and foreach over its array output.
- Project properties cannot have a blank value (the editor rejects
  empty) — model "unset" with a sentinel and gate on it.
- Project properties were historically UI-only; the Developer API
  (`/api/properties`, `prefix` required on GET) now creates/updates
  them — prefer the API and retire the UI dance.
- A property reference on a nonexistent property resolves blank
  (nil-tolerant) at runtime — recipes can reference properties before
  they exist.
- Condition LHS validation is stricter than step formulas: no `||`,
  no negation, no array literals in condition expressions (though the
  same constructs in step formulas are fine). Use the tool's operand
  vocabulary (`equals_to`/`contains`/`present`/…) with plain pill LHS.
- `set_condition` requires the TOP-LEVEL operand ("and"/"or") even
  with a single condition — a "Field required" error means you forgot
  it. The right-hand side of a string compare is the BARE literal, not
  an escaped-quotes form; a quoted value renders as an escaped literal
  that never matches.
- Condition breaks auto-generated as STRING compares lex wrong ("3" >
  "2") — force numeric comparison via ruby() on both sides.
- f-string interpolation in the builder accepts ONLY plain pill refs,
  not ruby() calls; embedded quotes inside f-strings must be
  single-quoted.
- AIRO-style reports of an internal Project ID that differs from the
  UI project ID are the SAME project under two identifiers — not a
  duplicate-project bug.

## How do recipe functions and skill recipes differ when called?

- A recipe-function call accepts ONLY true recipe functions (trigger
  `execute`). Recipes with a skill start-workflow trigger — including
  every Genie skill — fail start validation with "Recipe function
  with the id specified doesn't exist". To reuse a skill's logic from
  a recipe, call the underlying connector action directly and
  replicate its normalization.
- A synchronous function-call result is wrapped under a `result` key:
  reference callee outputs as `call_recipe_N['result']['field']`.
  Without that level the validator rejects the pill and runtime
  resolves nil. Schema attachment after push resolves validators even
  though input-schema reads show only the flow id.
- NEVER bind a bare whole-step reference in a ruby() call —
  design-time cannot compile it, so the binding resolves nil silently.
  Bind sub-paths.
- Recipe functions MUST be running to be callable, even though they
  have no trigger. Start functions after creation, not just calling
  recipes.
- AIRO-style pushes do NOT re-register a function's parameter schema;
  downstream callers keep validating against the stale schema. Known-
  good way: stop function → edit/push → open the function in the
  editor and Save (re-registers the schema) → fresh-pull callers →
  set the param per call site → push callers → hot-path verify.
- Recipe-function job RERUNS can start minutes late (pending queue);
  editor Test is the reliable invocation. Repeat-runs use the LATEST
  recipe version.

## How do you test recipes without waiting for the schedule?

- NEVER wait for the next tick. Stop → Test → review → start.
- Force-run is scheduler-trigger-only (HTTP 422 for skill or function
  triggers) and 422s on a stopped recipe — guard a force-run loop
  with a start-first check.
- On-demand testing of scheduled recipes: stop, run Test, review the
  trace, start again. The recipe-page cloud-icon menu is the
  deploy-to-TEST-environment wizard, NOT a test affordance.
- The deterministic-testing pattern for scheduled recipes: lift the
  body into a recipe function, keep the schedule as a thin shell, and
  drive a callable driver recipe — or use a fixture harness that
  invokes (webhook POST / force-run / API endpoint), polls for the new
  job, and asserts on the output.
- Slack-event webhooks validate Slack's envelope and IGNORE synthetic
  payloads; `code_input` datapills always arrive as strings.

## What do test-automation output checks actually compare?

Test Automation output checks are SNAPSHOT comparisons, not schema
checks: the runner compares a step's live output against recorded
literal expected values. Never record literals for steps returning
volatile live data (counts, listings, timestamps) — assert the
behavioral result step (the RETURN) instead, and keep only stable
checks. Monitor/error-handling retries do NOT catch this error class
(the comparison happens outside the monitored action). A monitor whose
happy path is green but whose alert-firing path never ran live is
unverified — force-fire each alert class once.

## Why is the MCP "Incompatible" badge showing on a new skill tool?

Adding a skill recipe as a tool on an Enterprise MCP server, the row
can show Incompatible. Hover the badge. Confirmed triggers:

1. The skill's telemetry step must hardcode conversation/call/genie
   IDs to empty strings — mapping them from the workflow context makes
   the tool incompatible.
2. The result schema needs a control type on every leaf field.
3. At least one required result field (an all-optional schema with a
   required field inside an array fails the step validator).

Also: empty static list values are silently dropped from saved step
config — omit the key and make the field optional. Tool `vua_required`
on an MCP server tracks the SOURCE skill recipe's "End user's
connection" flag; a skill pushed without it yields a non-VUA tool,
which can break callers that assert on a VUA-filtered count.
VUA-No is not connection MODE: the server's connections list can show
a tool among end-user-connection tools even when its VUA flag is No —
the flag is consent-gating, routing is separate; the package route is
the only path that edits it.

## How do you drive the Workato test dialog and modals reliably?

- Test-dialog pill fields are CodeMirror-backed: the CodeMirror
  instance attaches only after the field is FOCUSED (real click, then
  set the value via `element.CodeMirror.setValue(...)`); native
  value-setter + input events do NOT register there. Verify all
  fields with getValue() before submit; field order maps by position,
  not by the heading you clicked.
- Angular-based dialogs (`ng-star`) render a preview element, not an
  input: click the preview, fill the revealed textarea, and note that
  React-style input events do NOT bind state — `execCommand('insertText')`
  after focus sometimes works, but treat trigger-data dialogs as
  unreliable for scripting and prefer driving the real path.
- Save buttons stay DISABLED when a description was set via synthetic
  events (the form model never marks dirty) — focus, select,
  `execCommand('insertText')`, then Save enables.
- `find --locator text` clicks can navigate AWAY mid-dialog (wrong
  ancestor fallback grabs a nav link). Anchor clicks to specific
  selectors filtered by visibility (`offsetParent`), re-goto and retry
  on escape.
- JS `.click()` no-ops on React controls — use real mouse dispatches;
  confirm modals die on focus loss, so dispatch click → immediate
  snapshot, no eval between.
- Snapshot element refs go stale after every re-render — re-snapshot
  before each click; resolve menu option refs and click them within
  ONE chained command; page navigations reset the environment
  switcher to a default, and deep links 404 in the wrong environment.
- Orca-style eval: top-level `const` PERSISTS across evals in the
  same page ("already declared") — always wrap in an IIFE.
- Native computer-use typing/paste needs an on-screen window; browser
  runtimes without one make it unavailable for web content.

## What browser automation actually works for export manifests?

Export manifests historically had no API surface (the Developer API
has since added full manifest CRUD — prefer it). The UI mechanics that
work, when the UI is the only path: filter the source-folder combobox
by typing and click the project ROW; the step-2 tree is virtualized —
locate rows with a TreeWalker text-node search, scrollIntoView, then
real mouse clicks at bounding-rect coordinates with a pause between
mouse-down and mouse-up (back-to-back down+up silently fails to toggle
checkboxes ~50% of the time). In create-manifest dialogs, native-setter
textarea fills do not reach React state; use real keyboard events.

## What do export-manifest dependency closures force?

- Dependency closures are TRANSITIVE and FORCED: selecting a recipe
  that calls a function or genie event pulls the genie, every
  genie-attached skill, and their dependencies (tables, properties).
  The dependency checkboxes render enabled but unchecking does not
  stick — the selection recomputes. Connections are the removable
  class (environment-local).
- An asset can only be excluded by removing it from the source of the
  chain (e.g. detach a not-ready skill BEFORE creating the manifest).
  Manifest selections freeze at creation; update-manifest re-reads
  CURRENT asset content at build time but does NOT re-evaluate the
  closure — recreate the manifest when the closure itself changed.
- A manifest whose dependency assets carry blank folder/absolute_path
  imports as a blob. Every asset entry needs its REAL folder +
  absolute path: flows → folder from the recipe read; tables →
  paginate the table list and match numeric id; folders → walk
  parent_id to root.
- Never build a manifest from the top recipe's dep list alone — the
  deps list only shows DIRECT dependencies. For every flow included,
  read the export-manifest folder-assets on the FLOW's folder and
  union the deps; verify each new asset type's deps too.
- Recipes reference knowledge bases by ZIP PATH inside the flow JSON;
  the import resolver matches the reference against the zip's
  internal paths. The KB asset's manifest entry must carry zip_name =
  the EXACT path string the referencing flow stores (folder = same
  path minus the filename), or resolution fails even though the tree
  preview shows the file as included.
- Workato export manifests cannot be deleted; superseded ones can
  only be renamed (the rename often fails to commit — budget for
  living with the old name).
- A prod package import can report "Recipes require action: stop" —
  record the exact restart set BEFORE stopping, and verify
  environment auth before lifecycle ops.

## What breaks when wiring API-platform recipes?

- API-recipe auth header is `Api-Token: <key>` — Bearer / X-Api-Key /
  raw Authorization / "Token token=" all 401 even with a valid key and
  assigned policy.
- API-platform provisioning split: collections + endpoints are
  created via the UI wizard (or from an OpenAPI spec); endpoint
  enable requires the underlying recipe RUNNING. API clients + keys
  are API-creatable; the client needs BOTH the collection in its
  collection list AND a policy assigned — "Not assigned" means every
  call 401s even with a valid key.
- API Platform endpoints have independent activation state — starting
  the recipe is NOT enough; ingestion silently fails until the
  endpoint is activated.
- A 400 return-response needs the response REGISTERED on the trigger
  first: set the trigger's response entries (200 + the error status
  with its body schema), then the return step's status selector picks
  by name/code, and body keys appear in schema only after the status
  is set.
- Audit attribution: the API-platform trigger's context pills (calling
  IP, client name, access-profile name) turn per-call audit rows into
  forensics — store them in a JSON summary column.
- Dynamic-list response bodies coerce nil→"" silently: an f-string of
  a nil pill renders as an empty STRING in the JSON response, not
  null, even when the schema declares optional boolean. Fix: a
  per-key ruby() with a nil guard (`bindings.v == nil ? nil :
  bindings.v`) — booleans stay booleans.
- Recipe-side remedy for array type erosion: a ruby() passthrough in
  RETURN mappings keeps inner arrays real (typed Ruby binding beats
  string interpolation).

## How do you do raw recipe-code surgery when tooling is down?

When MCP folder/project resolution fails wholesale while the Developer
API token still works, recipe edits remain possible: GET
`/api/recipes/<id>` returns the `code` field as the NATIVE recipe JSON
— a tree of step nodes linked by `block` ARRAYS (foreach/if nest
children in their own block list). Clone an existing step node as
template (keeps schemas), assign new ids, edit input values, append to
the target block, PUT with `{"name": ..., "code": <json-string>}`.
HARD RULES learned by breaking it:

- PUT only while the recipe is STOPPED (running updates silently fail).
- Re-fetch the recipe before EVERY PUT — PUTs layered on a stale dump
  silently revert earlier fixes.
- Snippet `code` is a plain Ruby string; pill values in code input are
  serialized datapill strings.
- NEW steps with new pills fail start-validation (no run samples yet).
  In-place edits of existing steps validate fine — for diagnostics,
  REUSE existing steps (swap a query string and downstream snippet),
  then revert from the saved original.
- Builder sessions are real: a stale session id can deny folder access
  — a unique session fixes it (parallel-session era is real).
- `recipe_builder_save`-style calls ALWAYS create a NEW recipe — save
  once, then push on the session.
- Editor saves can rewrite a recipe-function's flow id from numeric to
  label, which start-validation then rejects as blank — repair via a
  field set and push before starting.
- `remove_input_field` fails "Field not found" when the field's value
  is literal None — set the whole parent object keeping only wanted
  keys, or remove/re-add the step.
- Genie-to-skill trigger conversion is impossible in place (trigger
  type locked) — plan one rebuild pass as new skill recipes.
- Recipe IDs are WORKSPACE-scoped; an ID from another environment
  yields a misleading "permission denied". Find the environment twin
  by listing the same-named folder.

## Why did the scheduler "never register"? (case-sensitive trigger units)

A clock scheduled trigger with a CAPITALIZED time unit (`"Minutes"`)
lands on an anchor-bearing path: validation demands a trigger-at time,
and every scheduled fire silently errors WITHOUT creating a job record
— invisible error accumulation until `trigger_errors_limit` auto-stop.
The symptom reads as "schedule never registered" (job list never
grows). LOWERCASE `"minutes"`/`"hours"` is the proven path (fires on
start + cadence, no anchor time needed). Check `stop_cause` on the
recipe object before concluding scheduler misregistration; reset-
trigger does not fix this class. Also: the Workato scheduler minimum
interval is 5 minutes — tighter cron fails START validation.

## Why did a fresh scheduler copy inherit the old schedule?

A CLI recipe-copy CLONES the trigger UUID — a "fresh-scheduler copy"
keeps the same schedule identity. Rebuild the recipe from scratch
instead of copying.

## Why do custom-cron triggers set via tooling fail validation?

Custom-schedule triggers set through builder config with a
trigger-at time can fail UI validation ("Invalid time format"; the
time-unit dropdown resets). An interval form validates and runs; set
the real cron in the UI editor at handoff, and when pushing a
custom-schedule trigger REMOVE the stale trigger-at/trigger-every
fields or the push complains about fields not in the schema.

## What is the stalled-scheduler probe that actually works?

`last_run_at` does NOT track scheduler ticks — a running recipe can
show a stale timestamp while green scheduler jobs run through the
moment. Correct probe: newest job via the jobs API
(`?per_page=1`), stall when its age > 1.5 × trigger interval.
A naive last-run sweep false-positives on weekly/monthly schedules.

## Why do recipes stop silently?

Recipes can stop with no stop cause, no failed jobs, no errors —
nobody's action. Enable the platform's recipe-stopped email alert,
and treat stale consumer responses as the canary.

## How do you make WQL (Workday) queries not lie to you?

- `businessProcessTransactions` is a proven discovery source for
  COMPLETED business processes (worker-scoped). Status vocabulary is
  "Successfully Completed" / "Rescinded" / "Canceled" / "Denied" —
  filtering on `= 'Completed'` silently returns 0 rows (valid source,
  wrong value).
- When a WQL response has ZERO rows, the `total` field is ABSENT — a
  condition evaluating `total > N` crashes the recipe. Guard with
  "total present AND > N".
- `approvedByWorkers` returns JSON of actual acting approvers right in
  the row — use it for approval-chain resolution instead of a SOAP
  event-details call.
- `dateAndTimeCompleted` is absent until terminal, so its presence =
  final.

## How do you avoid the empty-source mass-mutation hazard?

Any diff-based table sync MUST guard the "source returned nothing"
case: a "succeeded" job whose upstream call failed feeds an EMPTY list
into the diff, which then deactivates every row. Pass the source count
into the diff logic and short-circuit with a skipped-reason when it is
below the floor. Related: a silent Slack `channel_not_found` for a bot
missing from a private channel produces exactly this empty list —
check the upstream `ok` flag in job traces.

## How do you keep end-user credentials out of long-running integrations?

Ruling: any long-running integration uses a workspace-owned service
identity (a Slack app bot / integration service user), never an
individual's token — bus-factor and rebuild-on-departure risk.
Personal connections are acceptable ONLY for interactive/personal
automations. Production-ready = a net-new bot with its own
credentials, following the governed process. Temporarily borrowing a
colleague's connection to unblock a build is ALSO banned. See
slack-bot-connections.md for the full SOP.

## Why must agent-facing surfaces be environment-verified?

ANY conversational/agent surface reached through a signed-in session
(Slack apps, Genies, agents) must be environment-verified BEFORE use —
the signed-in corporate workspace is production. All testing belongs
in the designated non-production workspace unless a smoketest is
explicitly declared. When testing a specific agent, use ONLY the
designated test agent — any other agent is off-limits.

## What MCP-skill error envelopes do clients actually see?

Workato skill/genie recipes expose a return result but NO per-
invocation MCP isError — clients see success even from catch branches.
Compensate: a mandatory structured envelope
(`{success:false, error, errorCode, retryable}`) + a per-class
retryability policy + per-item fail-open for batch partials.

## How do you skill-test with no invocation API?

Skill recipes have no direct invocation API (test-case run/list only
— and even that is snapshot-based). The answer: add the workspace's
MCP server to an agent client, authorize the end-user popup once, then
MCP tool calls ARE real-caller test invocations (they even carry the
mock-genie context, matching production probe semantics). Pattern:
dev edit → MCP-tool invoke (403/404/happy) → job trace → ticket
comment. No browser anywhere.

## What do the adhoc-action schema limits force?

Builder tooling does not compile schema-designer output schemas for a
NEW adhoc HTTP action mnemonic — any pill reference to that step fails
start validation, regardless of re-set/re-push/repair. Adhoc
mnemonics registered by an EARLIER save keep compiling fine. Fix
pattern: prefer a NATIVE adapter action over a new adhoc action when
downstream pills must be referenced — list the adapter's operations
and inspect its schema by placing the step.

## What is the WAF-redirect trap on newer API routes?

NOT scopes, NOT domain: the Workato WAF 302-redirects requests with
python-urllib default headers (default UA + identity encoding + no
Accept) to the sign-in page on NEWER API routes — even with a VALID
bearer token. Legacy routes don't behave this way, which produced a
confusing split. Fix: always send a real User-Agent + `Accept:
application/json`. The debugging method that cracked it is worth
reusing: cross-test headers BOTH directions (curl with urllib's
headers breaks; urllib with curl's headers fixes) — isolates
header-level vs TLS-fingerprint causes in two calls.

## What do the Snowflake-backed MCP tool limits force?

- Recipe-function MCP tools have a ~4-minute CALLER-side timeout; a
  heavy monolithic query dies at the caller cliff even when chunked.
  Signature: failures at almost exactly 4:00 with a
  connector-not-responding error. The connector's own result CACHE
  explains intermittent instant successes (a timed-out call keeps
  computing, caches the result, and an identical retry returns
  instantly — hence "you can try again").
- The fix that works: cursor pagination at the MCP layer — each call
  = one partition-pruned bounded query + `next_cursor`; the caller
  iterates to null. Do NOT add a run-long-query async action — the
  caller window still kills it.
- `Select rows using custom SQL` silently defaults to 100 rows when
  `limit` is unset — a page-heavy tool truncates without warning.
  Set limit explicitly.
- A cursor page can still exceed MCP CLIENT result caps (~32K tokens):
  cap pages by row count, trim the widest column in SQL
  (e.g. SUBSTR(description,1,200)), and chunk within a bucket
  (cursor = bucket*100 + chunk). Keep FULL row shapes — prefer full
  field names and more calls over terse keys.
- Cursor-jump pagination reads as a bug to smoke testers (after a
  short page, next_cursor JUMPS to roll to the next bucket;
  sequential pages past the data are empty BY DESIGN). Fix = caller
  guidance ("follow next_cursor verbatim; stop only at null"), and —
  better — an explicit month/date-range param NEXT TO the cursor so
  humans can address buckets directly. Rule of thumb: cursor =
  continuation within a bucket (agents handle natively); range param
  = bucket addressing (humans handle natively).
- Hot-path final form: a scheduled recipe loop pre-aggregates into a
  data table; the MCP tool = keygen query + one bounded read = whole
  bucket per call, ~2s, next_cursor always null. Clients never
  paginate.
- Batch row-writes over a data-table bridge run ~1 row/sec — a
  large backfill takes hours (fine in background, not interactive).
  Reads stay fast even under write load.
- CRITICAL timezone rule: never derive month keys from string slices
  of platform-rendered timestamps (the same instant crosses a month
  boundary between the platform's rendered offset and the warehouse
  session timezone). Writer and reader must compute the key in the
  WAREHOUSE (DATE_TRUNC on CURRENT_DATE).
- Dev vs prod connections on the same warehouse run materially
  different plans — performance conclusions need the prod connection.
- A scratch probe recipe in the dev workspace (clock trigger + pasted
  SQL + real connection, then force-run) is the fastest ground truth
  for SQL semantics without touching the hot path.
- OFFSET paging over a living warehouse can shift row identity
  mid-iteration (handles churn between runs) — ORDER BY keeps page
  order stable per call; cross-call row drift is inherent to the data
  layer.

## What data-correctness patterns recur across connectors?

- First-1000 starvation: a capped UNFILTERED read returns insertion
  order and silently drops rows that sort late (hex UUIDs) — filtered
  reads are sort-independent, capped unfiltered reads are not; always
  sort descending on recency reads.
- Duplicate-write class: an unconditional write AFTER an if/else
  inside a loop executes every iteration — verify by trace that
  exactly one write per candidate occurs.
- Delta-accumulation double counting: incrementing stored counters
  with per-run deltas anchored on a window end double-counts
  post-window events — use an idempotent window RECOUNT (recompute
  each run, skip when unchanged).
- Published-schema default vs action-fallback drift: schema-aware
  clients bypass a recipe's action fallback because the published
  trigger schema still declared the old default — keep them in
  agreement and audit both after any default change.
- Merge-boolean decoupling: when a recipe emits a "today" flag and a
  "next N days" flag from the same date list, the window test MUST
  exclude today, or the flags are nested (today-in-range makes
  within-N-days always true). Open-ended date ranges need an explicit
  end-date guard — an unconditional on-leave implies in-window keeps
  leavers flagged forever.
- Contract fix class-sweep: a projection fix on one variant leaves
  sibling variants broken (each carries its own input-contract
  enumeration) — contract fixes sweep EVERY variant AND its enums,
  then verify one per variant. Registry checksums are PER-SERVICE
  (one global checksum goes stale for mutations).
- Connection-lost blocks recipe START with no API/CLI bypass (only
  owner reauthorization repairs); a quota-exhausted OAuth connection
  fails validation too while the parent job stays Successful under
  catch.
- Empty-string padding tax: most scalars in a default list response
  can be empty strings — apply skip semantics to optional scalar
  mappings (keep booleans/arrays/IDs directly mapped so valid `false`
  survives) + a compact default projection.

## Which reusable techniques are proven?

- Forced-pagination proof: temporarily shrink page size to force loop
  iteration across the old boundary, then restore — proves
  continuation-token behavior without fabricating a huge fixture.
- While-loop scalability: seeded-first-page + repeat-while-continuation
  loops asserting each token advances (a non-advancing token fails at
  the execution boundary).
- Stress-test methodology: three escalating profiles — standard
  (ramp + sustained waves + mixed functional/security), validated
  ceiling (zero-error concurrency), extreme overload probe (first
  timeout boundary) — each ending with a recovery probe, all on one
  pinned version, with requested/completed/valid accounting.
- Latency attribution gate: attribute end-to-end across
  routing/backend/serialization/transport/rendering BEFORE optimizing.
  Merge an internal function into the public skill only when the
  function/wrapper boundary consumes ≥20% of backend latency. Add
  only the smallest proven-necessary index.
- Snapshot architecture (generalizable): versioned snapshot + derived
  search index + denormalized closure index, built under an
  unpublished version, validated (counts/refs/cycles/hash parity),
  atomically promoted via one shared pointer, previous version
  retained for rollback; never expose partially built indexes or mix
  versions.
- Benchmark discipline: a run contaminated by a concurrent session
  produced false regressions — rerun only when the target is quiet;
  measure the tool-catalog listing cost separately; label character
  estimates as estimates.
- Red-team false-success pattern: a mutation call can return success
  with an EMPTY created ID (nothing created — proven by a before/after
  inventory). Verify mutations against INDEPENDENT state, never the
  tool's own success flag; a guard should stop an update from
  deleting the original when replacement creation fails.
- "Normalize at the proposer, gate hard at the reviewer": the seeder
  strips punctuation/collapses whitespace; the promotion gate stays
  strict as defense in depth. Honest-evidence rule: a masked denial
  probe proves the entitlement BOUNDARY, never positive tenant data;
  fixture-backed mutation tests are contract proof, not tenant proof.
- Folder taxonomy convention: shared functions + infra tables in a
  Functions folder, schedules/purge/TTL in an Automation folder;
  moving an asset to a `zArchive` folder auto-removes it from dynamic
  monitoring scope (retirement, NOT a staging area); never delete
  platform-created empty folders (unrecreatable).
- RecipeOps dynamic-discovery monitoring: project-scoped discovery
  (no hard-coded allowlist), alert evaluator materializes deduped
  state, every bounded source checks its continuation token — a
  second page emits ONE capacity alert and stops (fail-closed, never
  partial-scope evaluation).
- Jira-connector traps: never copy custom-field IDs across tenants;
  select-type fields need internal option IDs (never the display
  label — preserve unresolved values in the description); control
  characters in Slack-copied text make raw JSON invalid — sanitize at
  the outbound boundary and fail closed.
- Genie platform facts: `send_business_event` gives the model NO
  trustworthy business-event flag (callback markers are display
  only); there is no truly reply-silent genie Slack response (empty
  output renders a literal placeholder — supported clean pattern is
  one compact root notification + one exact preformatted reply, then
  stop); Decision Models do NO natural-language intent extraction
  (ordered rules on structured input only — safe as a shadow
  evaluator, never a dispatcher); after editing/restarting an
  attached skill recipe, wait for skill re-registration and ALWAYS
  test in a NEW source conversation (existing ones retain prior
  state, failures, and the tool catalog they started with); a wrapper
  job description must MIRROR the source MCP contract; Slack does
  not render Markdown pipe tables (convert to compact bullets
  without changing values).

## What agent-loop AI-gateway limits bite?

- Non-streaming gateway calls hit a hard ~60s first-byte deadline:
  large generated tool arguments can cross it. Mitigations:
  compact/fresh long sessions before prefill nears the deadline, pass
  references/hashes instead of regenerating large blobs in one tool
  call, keep SSE streaming, prefer the model class that survives
  larger arguments.
- Judge caching from TRANSCRIPT usage aggregation, never from
  headless single runs — a single-turn run is always a fresh cold
  conversation (cache reads = 0), so that method produces a wrong
  "zero caching" verdict. Provider-side prefix caching makes the
  stable per-conversation prompt prefix nearly free from turn 2.
- Tool search may be unavailable behind non-first-party gateways:
  all tool definitions load upfront, so pruning unused servers cuts
  every cold-start cost. Exact-name deny lists work; wildcards may
  not.

## What do event-driven command loops (Slack directives → agent) require?

- Self-ingestion filter: posts made by the automation's own identity
  carry that identity — the poll leg must skip its own tracked
  messages or it ingests its own digests as directives. Every post the
  system makes must be recorded in a ledger the filter can read.
- Concurrency: scheduled ticks every 60s while a drain runs minutes —
  guard with a single-flight lock so later ticks exit quietly, and
  per-row error handling so one poison row is marked rejected instead
  of crash-looping the tick.
- Event path vs poll path: after scope changes or reinstall, the
  poll leg (channel history on an interval) is the reliable ingest;
  the event path needs a fresh real message to test. Event
  subscriptions on an OLD unused app keep burning workspace-wide
  deliveries — disable them.
- Scope/subscription changes on a Slack app are NOT self-serve:
  saving event subscriptions triggers an ADMIN APPROVAL cycle for the
  reinstall before new scopes take effect. Factor the lead time in —
  don't promise same-minute delivery until the reinstall is approved.
- EVERY app reinstall rotates the bot token: the stored copy goes
  stale with confusing missing-scope errors (the scopes were fine;
  the TOKEN predates them). Re-read the token from the install page,
  verify with a history call, then update the credential store.
  Slack may also update scopes in place on reinstall — verify the
  same token string before swapping.
- Directive grammar checks must be word-boundary exact on the first
  token — a substring check rejects every valid directive that
  happens to contain a fragment.
- Never subprocess a bare binary name in a scheduled job — resolve
  absolute paths at module load (the scheduler PATH differs from the
  interactive shell).
- Word-boundary and dedupe discipline aside, record feedback
  visibly: a watcher that marks rows done with zero visible output
  looks broken even when it works. Acknowledge in-channel as the bot
  after every directive.
- A documented-but-unbuilt fallback is worse than no fallback: never
  pre-document mechanisms that don't exist yet.

## What governance habits keep the estate honest?

- Descope recording: when a stakeholder descopes a concern ("skip X,
  build everything else"), RECORD the descope in the governing ticket
  (one status comment: what was descoped, why) BEFORE continuing.
  Descoped requirements resurface later as surprises; the ticket must
  show they were consciously dropped.
- Naming: never leave an asset named TEST outside the dev cycle —
  even dev work renames TEST-named consumers to their permanent names
  before calling them done. Test fixtures that exist only for UAT get
  deleted once their evidence is recorded, not parked with TEST
  names.
- Instrument the FULL outcome matrix: when adding telemetry to a
  branch, cover success + rejection + failure in the same commit.
  Failure-only recording ships, and outbound success stays invisible
  for days.
- Unmasking (`mask_data=False`) on a failing step is the decisive
  diagnostic move when a failure cannot be diagnosed from metadata
  alone — unmask only when the payload carries no secrets/PII, and
  diagnose from the real payload.
- Before declaring a stopped/errored recipe function matters, grep
  for its references in the suspected caller — functions can be dead
  code (zero references, zero jobs is the tell).
- A "green job" is not evidence of correctness: an alert path that
  never fired, an audit row never written, and an untested
  error-handling branch have all shipped green. Proof at the end
  means the specific expected trace lines exist.
- A tool's inconsistency report can be STALE (snapshot from a
  different window) — verify against live pill reads before treating
  reported inconsistencies as real.
- Test fixtures get consumed by their own harness: a test run can
  queue REAL deletions of runtime children it references. Recreate
  fixtures deliberately (the connector requires the provider even for
  child connections, and root-folder assignment is rejected), and
  keep fixture connections under a project folder.
- CLI/connector version traps: `recipes list` can fail on pydantic
  None handling when a project property drives telemetry state;
  double-encoding a JSON body (passing a string into an encoding
  client) 400s every request — pass the dict, let the client encode.
