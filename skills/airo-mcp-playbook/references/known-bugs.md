# Known bugs — recipe-builder mechanics, legacy migration, packaging

Load this file when doing recipe-builder session work, migrating a legacy
`workato_genie` skill, wiring blueprints, or importing/inspecting a Workato
package. For the six highest-severity data-loss/wrong-identity bugs, see
SKILL.md directly — they aren't repeated here.

## Resolved bugs / regression watch

- **VUA connection reset after recipe edits/pushes — intermittent regression
  watch.** A reset to the pushing service/ISU connection was confirmed fixed
  on 2026-08-05, but later personalized-recipe pushes showed intermittent
  loss of "End user's connection." Expect persistence, then verify the saved
  mode after identity-sensitive pushes when an authoritative surface exposes
  it. Repair only observed drift and retest caller scoping; do not blindly
  re-select after every push. Capture asset ID, environment, push path, and
  before/after state. New imports and first-time setup still need their
  intended connection configured.

## Recipe-builder session & editing mechanics

- **Session goes stale after push.** `recipe_builder_*` edits fail after a
  `recipe_builder_push` until you re-pull the recipe into the session
  (`recipe_builder_pull`).
- **Can't push a running recipe.** Stop it first in the Workato UI — no
  stop/start tool exists in AIRO MCP.
- **`recipe_builder_update_step` cannot reliably swap a trigger's
  connector/action.** The DSL preview updates but `get_input_schema` stays
  on the old action's schema (a real bug, not display lag) — input fields
  get flagged `InputFieldNotInSchema`. Fix: delete and rebuild via
  `recipe_builder_init` + a fresh `add_step(type="trigger", ...)`. Note
  `recipe_builder_remove_step` refuses `position < 2` — step 1 can't be
  removed either, only replaced by starting over.
- **`while_condition` can't be removed independently** — delete or empty the
  parent `repeat` instead.
- **`declare_variable` schema staging order matters.** Set `variables.schema`
  ALONE first, THEN `variables.data.*` in a separate call — setting both at
  once can fail to register the output datapill schema, producing a
  downstream `DatapillInvalidPath`. Also, `declare_variable`'s input shape
  itself varies by session state (sometimes simple scalar `name`/`type`/
  `value`, sometimes multi-field `variables.schema`/`variables.data.*`) —
  always call `recipe_builder_get_input_schema` fresh per step rather than
  assuming the shape from a previously-pulled recipe's DSL.
- **Inserting a step renumbers every step after it.** Re-verify step numbers
  with `recipe_builder_show` before editing a later step's fields.
- **`job_get` collapses loop iterations** (shows only one). To inspect
  per-iteration results across a loop, use `job_list` instead — job titles
  often carry an identifying value (e.g. recipient email) per iteration.

## Blueprints

**Order-sensitive.** Create placeholders (`blueprint_create`) first, build
the real assets, then bind each one with `blueprint_link_asset`. Linking
before the asset exists fails or orphans the binding.

## `session_id` collisions between `recipe_builder_*` and `genie_*`

Reusing one `session_id` across an active `recipe_builder_*` session AND a
subsequent `genie_update` call polluted the `genie_update` request URL with
the recipe's full JSON payload → `414 Request-URI Too Large`. Use a
dedicated `session_id` for `genie_get`/`genie_update`, never one an active
recipe-builder session is using.

Also: `genie_update` on a brand-new `session_id` that never called
`genie_get` first fails with `"Genie with ID '<workspace_id>:<session_id>'
not found"` — seed the session with a `genie_get` call once before
`genie_update` on that same `session_id`, the same pattern recipe sessions
need (`recipe_builder_pull`/`init` before edits).

## Legacy `workato_genie` → `workato_skill` migration

Skills built before the platform migrated to `workato_skill` still use
`workato_genie.start_workflow`/`workflow_return_result`. They run fine as
Genie skills, but the MCP-server attachment UI flags them **"Incompatible"**
— tooltip reason: *"Uses Call ID, Conversation ID or Genie ID datapill"*
(`start_workflow_1['context']['call_id']`/`['conversation_id']`/
`['genie_id']`). Those context fields only exist inside a live Genie
conversation; a generic MCP client invocation has none, so the platform
blocks attaching a skill that depends on them.

Migration steps:
- `recipe_builder_add_step` refuses new `workato_genie.*` steps outright
  ("deprecated and cannot be used") — new steps must use `workato_skill`.
- Use `recipe_builder_update_step` to change the trigger (step 1) and every
  `workflow_return_result` action to `workato_skill`. This **wipes** the
  trigger's `parameters_schema_json`/`result_schema_json`/`description`/
  `requires_user_confirmation` fields and every changed action step's
  `result` field. Re-set all of them immediately via
  `recipe_builder_set_input_field`, using `recipe_builder_show()`/
  `get_datapills` beforehand to capture the exact prior values.
- Strip any `call_id`/`conversation_id`/`genie_id` datapill usage elsewhere
  in the recipe (e.g. a file-naming expression) — replace with something
  MCP-safe and still unique, like `job_context['job_id']`.
- The recipe must be **stopped** before `recipe_builder_push` accepts the
  edit — pushing a running recipe errors "Cannot edit recipe... because it
  is running." No stop/start tool exists in AIRO MCP; manual UI step.
- After a successful migration + push, the MCP server's tool listing can
  still show stale info (old description, "Incompatible" badge) even though
  an independent `recipe_get` confirms the backend updated — a display
  cache issue, not a failed push. There's no confirmed auto-refresh path;
  re-syncing the MCP server row or manually updating its description may be
  needed.
- Ruby `ruby()` formulas are a restricted subset (see
  `references/ruby-dsl.md`) — relevant here because nil-safe guards on
  optional nested fields need the `present`/`blank` pattern, not `.dig`/`||`.

## Packaging / multi-copy gotchas

- **Exported Workato MCP packages can be missing recipe assets
  entirely.** An import error that looks like a "dependency ordering"
  problem can actually mean the zip contains only `.agentic_skill.json`
  pointer files + connection + `project.json` metadata, with zero
  `.recipe.json` files anywhere — even though each skill JSON's
  `references.recipe_id.id.zip_name` points to one. Confirm by direct
  filesystem search for `*.recipe.json` in the extracted package before
  assuming an import-order or manifest-structure fix will work — it won't;
  the real recipe logic only exists in the customer's live workspace and
  requires a re-export with "include recipe/dependencies" checked.
- **Duplicate copies of the same skill set can exist across different
  projects** — a dev copy and a different team's production deployment can
  share near-identical recipe names/logic with no distinguishing suffix,
  living in different project IDs. Always confirm which project/recipe ID
  an MCP server config actually points to before trusting test results
  against it — a local MCP client config can silently point at the wrong
  copy with no obvious symptom until behavior looks "already fixed" or
  "still broken" in a confusing way.

## Project properties

- **No create/set tool exists.** `property_list` is read-only — properties
  must be created/edited manually in the Workato UI (Project settings →
  Properties).
- Reference them in a recipe as a plain expression:
  `project_property['Property Name']` (no `ruby()` wrapper needed for
  simple substitution). This is the pattern for making environment-specific
  IDs (service-desk IDs, base URLs) portable across dev/prod without a
  recipe edit.
- **Property names are exact-match, case-sensitive.** Always confirm the
  exact created name via `property_list` before assuming a binding
  resolves — a case mismatch (e.g. referencing `Anniversary Lookahead Days`
  when the user created `Anniversary lookahead days`) silently no-ops to
  whatever hardcoded fallback the recipe has, forever, with no error.
- **ruby() formulas with block syntax (`select { |r| ... }`) inside a mapped
  list-comprehension string are silently dropped at set time** — the builder
  accepts the call, then persists the field as `""` (confirmed on a dev
  recipe, 2026-09-12). Simple ruby() with scalar/deep-path bindings inside a
  comprehension DOES persist. Fix: move correlation/lookup logic into a custom
  connector helper action (pure Ruby, full JSON) and keep comprehensions to
  plain f-string datapills over a parse step's output.
- **Brand-new step rejects the first set_input_field call** with "not available
  in the current input schema" even when the schema is correct; one
  `get_input_schema` call warms it and the identical retry succeeds (seen
  repeatedly 2026-09-12).
- **New connector actions need cache warming before AIRO can map their
  fields**: a fresh recipe_builder session sometimes resolves them; otherwise
  open the action once in the browser editor's action picker (fetches the
  fresh schema), then retry AIRO. Same trap family as repeated
  schema-deferral cases.

## recipe_builder_save creates a NEW recipe ID on every save (confirmed 2026-09-25)

- **Symptom:** In a dev workspace (AIRO MCP), each `recipe_builder_save` call
  on an active builder session returned "Recipe created successfully" with a
  NEW recipe ID (one probe recipe produced four distinct IDs across four
  saves) instead of updating the previously saved asset. No error; easy to
  end up with several near-identical copies.
- **Workaround:** Treat the newest save as canonical; verify with `recipe_get`
  (check connection binding + latest steps on the newest ID). Rename
  superseded copies "(DUPLICATE - delete me)" via
  `recipe_builder_update_asset_metadata` and delete them with the public
  REST API: `DELETE /api/recipes/:id` (recipe must be stopped first; works
  with the workspace API token — confirmed 2026-09-30 on 14 duplicates).
  AIRO the MCP server has no delete tool, but the API does. Watch for this
  whenever a session does multiple save cycles.

## Callee parameter-schema staleness: AIRO push does NOT re-register a recipe function's schema (2026-10-08)

**Bug:** When a recipe function's `parameters_schema_json` changes via AIRO
push, downstream recipes' `call_recipe` steps keep validating against the
callee's OLD schema. `recipe_builder_set_input_field` on the caller rejects
the new param ("not available in the current input schema"), and
`recipe_builder_update_step` (re-selecting the same connector/action) does
NOT refresh it. A fresh caller pull doesn't help either: the schema comes
from the platform's function registry, which the AIRO push path does not
update.

**The known-good way (works through the cache, never around it):**
1. Stop the function recipe; AIRO pull → edit the trigger's
   `parameters_schema_json` → push. Verify the saved code carries the change.
2. **Open the function recipe in the editor and Save it** (browser: Edit →
   Save). The editor save re-registers the function's schema platform-wide.
   This is the missing cache invalidation, not an optional step.
3. Fresh-pull each caller; the call sites now accept the new param
   (`parameters.<name>`). Set values, push each caller (stop → push → start).
4. Hot-path verify with a real call and read the effect at the consumer.

**Cost of skipping step 2:** you either ship a partial state (some call
sites unpatched — unacceptable) or invent workarounds (per the standing
rule: never build around a stale cache; fix the cache). Bonus: the strict
start-validation after a schema re-registration can surface LATENT bugs the
stale cache hid (case in point: a `workflow_return_result` with blank
result fields returned an empty response on a fail-closed path — fix those
properly, do not just silence the validator).

## Clock scheduler: `time_unit` is CASE-SENSITIVE + `wt recipes copy` clones trigger UUIDs (2026-10-07/08, three dead recipes)

**The bug.** A clock `scheduled_event` trigger created with
`time_unit: "Minutes"` (capitalized) lands on an anchor-bearing schedule
path: `trigger_at` becomes a REQUIRED field at start validation, and — the
silent part — every scheduled fire ERRORS WITHOUT MATERIALIZING A JOB
RECORD. The jobs API shows a job list that never grows (reads exactly like
"the schedule never registered"), trigger errors accumulate invisibly, and
the platform auto-stops the recipe with `stop_cause=trigger_errors_limit`.

**What works.** `time_unit: "minutes"` (lowercase) selects the anchor-less
cadence path: the extended input schema carries only `trigger_every` +
`timezone` + `start_after`, start validation needs no `trigger_at`, and the
scheduler registers, fires immediately on start, then fires on the cadence.

**Evidence (one scheduled-monitor recipe lineage, dev):**
- The original recipe (capitalized "Minutes" + a fixed trigger_at time): 12
  succeeded scheduled jobs, then trigger_errors_limit stop; the last visible
  failure was a Slack 404 — but the killer errors left NO job records.
- The `wt recipes copy` "fresh-scheduler copy": SHARED the original's
  trigger UUID (copy clones it), same capitalized time_unit, same stall.
- `wt recipes reset-trigger` → success:true, no effect. Changing the anchor
  time and interval → no effect. A full AIRO rebuild keeping
  "Minutes" → no fire either.
- A fresh `recipe_builder_init` build (`time_unit: "minutes"`,
  `trigger_every 10`, NO trigger_at): scheduler fired immediately on start +
  recurring tick at +10 min, both succeeded. Logic verified byte-identical
  by code-JSON diff.

**Diagnostic signature:** recipe running:false with
`stop_cause=trigger_errors_limit`, job list that never grows while the
recipe was left running. Check `stop_cause` on the recipe object BEFORE
concluding "the scheduler never registered."

**Related non-ASCII trap (hit during the rebuild):** passing emoji/unicode
characters through `recipe_builder_set_input_field` MCP args can store
literal `\uXXXX` escape TEXT in the Ruby snippet. `—` etc. happen to
render correctly in Ruby double-quoted strings, but surrogate-pair escapes
are INVALID Ruby. Re-set the field with literal UTF-8
characters and verify with a diff of the stored code.

**Also verified while investigating:** `wt recipes force-run` 422s on a
stopped recipe ("Start the recipe before triggering an on-demand run").
Any fleet-style force-run job needs a start-first guard.

## Push to a RUNNING scheduled recipe can silently kill its trigger registration (2026-10-08)

A scheduled monitor recipe (fresh trigger UUID, lowercase `minutes` — see
the case-sensitivity entry above) ran clean for 19 jobs. An AIRO push landed
in the evening; the scheduler double-fired seconds later, twice, then went
permanently silent: recipe reported `running=True`, `stop_cause=None`, zero
new jobs for 3+ hours. The trigger registration died with the push and
nothing surfaces it.

**Symptom triad:** running=True + stop_cause=None + newest-job timestamp
frozen well in the past.

**Repair:** `wt recipes reset-trigger <id>` (re-anchors the scheduler). Then
verify SUSTAINED firing: one self-fired job proves the body, TWO at the
expected cadence prove the trigger (a forced run does not prove registration).

**Practice:** after ANY push to a running scheduled recipe, force one fire or
reset-trigger before trusting the schedule. Cheap alternative: check the
newest-job timestamp right after every push.

**Do not confuse with:** the heartbeat EXISTS but is stale (the stall-watch
recipe's stale-row branch) vs "no heartbeat row at all" (its `hb.nil?`
branch fires only when the row truly doesn't exist — during a mid-deploy
window where the heartbeat writer isn't live yet, that alert is CORRECT).

## JSM create-request rejects non-form fields (labels) — set post-create (2026-10-08)

`POST /rest/servicedeskapi/request` `requestFieldValues` only accepts fields on the request-type
form. `labels` → `400: The field 'labels' is not valid for this request type '<name>'`. Never add
labels (or any form-external field) to the create payload. Pattern: create via JSM API, then a
BEST-EFFORT `jira.update_issue(labels=…, project_issuetype="KEY--IssueType")` wrapped in its own
error-swallowing monitor (retries=0, empty catch) so a label/tag failure cannot retroactively fail a
successfully created ticket. `project_issuetype` uses "KEY--IssueType" IDs — discover via
`recipe_builder_get_picklist(field_path="project_issuetype", parent_id="<Folder>@@<KEY>")`; a JSM
request type maps to a real issue type.

## recipe_builder error_handling: catch-targeting sequence (updated 2026-10-09, verified on prod-parity ports)

Behavior has SHIFTED from the 2026-10-08 note below: a `move_step` into a
fresh error_handling block now lands the action in the **TRY** (was: catch).
Current verified mechanics for building a monitor with a NON-empty catch:

1. `add_step(type="error_handling", position=N, parent=<enclosing step>)` —
   type ONLY, never pass `action=` (that corrupts the block: renders "Setup
   done" but save/start hard-fail). Block occupies N (try) + N+1 (empty catch
   placeholder).
2. `move_step` the action to (new_position=N+1, new_parent=N) → lands in the
   TRY; catch stays empty (`pass`). This is the fail-open wrap shape.
3. For a step inside the CATCH: `add_step` the action at the catch's
   placeholder position (parent=block) — it ALSO lands in the try; then
   `move_step(position=<action>, new_position=<catch_step+1>, new_parent=<catch_step>)`
   where <catch_step> is the catch's current placeholder number. Children of
   the CATCH's step number are the catch body. Verified twice on prod.
4. Empty catch renders `pass` and flags `structure:EmptyBlock` — intended for
   fail-open wrappers; pushes fine.

Also: brand-new `workflow_return_result` steps reject the first
`set_input_field` ("schema lists no fields") — warm with one
`get_input_schema` call first. Boolean result fields (ok/retryable) are
TOGGLE fields; to match the `"false"` string form set them through
`toggle_config={"result.ok": false, ...}` (false = secondary free-text
control) with the string value.

## set_input_field_v4_mode mode="json" stringifies hash-shaped fields (2026-10-08)

Setting a whole hash-shaped field (e.g. an adhoc action's `input={"schema":…,"data":…}`) with
mode="json" serializes it into a single f-string TEXT value — pill mappings and the schema
refresh break ("Missing sticky field", `TypeError: String does not have #dig`). Fix: re-set the
field via plain `recipe_builder_set_input_field` with the structured DSL value (datapills as
bare expressions, schema as a quoted string) — this restores the hash form and re-validates.
