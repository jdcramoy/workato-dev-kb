# Delivery and release controls

## Test harness discipline

Build branch coverage around a known fixture and an explicit safety boundary.
The harness should be stopped by default, point only at an approved Development
target, use one controlled recipient or endpoint when delivery is involved, and
have a known cleanup step. If a past production/dev job contains the precise
failure signature, replaying it is stronger than inventing look-alike data, but
only after confirming that replay has no unintended external side effect.

Inspect the whole chain: trigger, function/worker calls, table or claim state,
and consumer/delivery result. For a schedule, test it manually or with one
bounded interval; do not let a newly built harness continue generating traffic.

After UAT, stop the harness, preserve the result links in its description or
project memory, and move it to `zArchive`. Archive is recoverable and keeps
future regressions reproducible.

Stopping is not proven by an accepted click or an intermediate "Stopping"
label. Complete any dependency-impact confirmation, reload the recipe, and
verify that the authoritative control now offers **Start recipe**. Apply the
same rule in reverse when restoring the intended running state. Stop only the
target recipe unless the change contract explicitly includes dependents.

## Lifecycle manifests

A Workato lifecycle manifest contains only the changed assets and required
dependencies. Before building it, inventory each selected item and explain why
it belongs. Exclude temporary harnesses, test cases unless deliberately needed,
test/table data, stale prototypes, tags, secrets, and unrelated project
properties/connections by default.

Treat every `dependent assets not found in source folder` warning as unresolved
until the referenced asset ID is inspected. Determine whether the dependency is
outside the selected hierarchy, missing from the export, or intentionally
environment-local. Do not approve a manifest merely because the named asset is
visible somewhere in the project.

If a manifest already exists, update that Workato artifact in place and verify
its current selection rather than creating a duplicate. Asset edits made after
the original selection may require the existing manifest to be refreshed; a
comment, local inventory, or hand-written JSON is not a manifest update.

Promotion creates environment-local asset identities. Never expect Development
recipe, folder, connection, or table IDs to remain valid in Production. Portable
logic should use promoted dependency mappings, source-event identity, canonical
project paths, or an environment-local runtime registry. Project properties are
appropriate for true runtime configuration, not for maintaining comma-delimited
asset inventories.

After import, validate environment wiring before enabling traffic:

- mapped service/end-user connections and their authorization state;
- externally visible sender/service identity and access to private destinations;
  use a durable non-personal app/service connection for long-lived automation,
  then verify the actual delivered sender rather than the connection label;
- project-property names and values, including case-sensitive references;
- required tables and schemas without carrying nonessential historical rows;
- expected recipe version and running state;
- required mapping pills and literal design-time associations after import;
- one bounded smoke test that demonstrates the imported behavior.

Copy/import can place assets in a project root or create an unintended parallel
folder. Verify every imported asset's final project/folder and identity by ID,
not only by name. For MCP changes, also verify the exact expected tool count and
names from a fresh client discovery session; the server UI and an existing task
can hold different cached inventories.

Also verify environment-specific external behavior. Production success and
failure notifiers should be active only where intended; Development emitters
must be stopped or explicitly destination-gated when they share real channels,
mailboxes, queues, or recipients.

Never call a local file or hand-written manifest plan a Workato lifecycle
manifest. The Workato export artifact is the source of truth.

## Production hotfixes

Production is not an implicit continuation of Development authorization. Before
an approved hotfix, capture the live version, exact edit surface, current
running state, dependencies, rollback version, and validation fixture. Change
one bounded unit at a time. Restore the prior running state only after the
saved state is independently verified.

Use a constrained smoke test: one known fixture, no broad schedule activation,
explicit recipient/endpoint control, and a pre-agreed cap. Inspect backlog,
lookback/window, queues, and claim/dedup state first; a schedule's serial
concurrency does not guarantee historical events will not be selected again on
later runs.

Mirror a proven Production-first correction into Development before the next
normal promotion so lifecycle deployments do not reintroduce the incident.
Compare both live versions first: Production may contain the newer fix, or
Development may already contain an equivalent implementation. Never overwrite
either side merely because its environment label implies an ordering.
