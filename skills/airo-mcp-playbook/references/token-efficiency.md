# Token-efficiency playbook for AIRO MCP

Load this file whenever a task involves more than a couple of AIRO tool
calls — a recipe build, a multi-step investigation, or anything with a
push/test/fix loop. The single biggest token driver in AIRO work is not the
number of tool calls, it's the **size of individual tool responses** — a
full recipe DSL, a full job trace, a full connector operation list, a full
data-table dump. Optimize for avoiding redundant large fetches first;
everything else is secondary.

## 1. The core principle: response size dominates call count

Ten small, targeted `recipe_builder_*` calls cost less than two calls that
each return an entire recipe's DSL or an entire job's execution trace. A
recipe with 20+ steps, or a WQL job returning hundreds of worker rows, can
run to thousands of tokens in a single response. Before making any call
that returns a large structure (`recipe_builder_pull`, `recipe_get`,
`job_get`, `data_table_get` on a big table, `recipe_builder_list_connector_operations`
on a connector with hundreds of actions), ask: is the *whole* object needed,
or just one field/step/row out of it? Prefer the tool or parameter
that narrows the response.

## 2. Pass an explicit ID or link instead of a name

Resolving a name to an ID costs a search or list call — `project_list`,
`folder_list`, `recipe_list` — plus scanning the (often unfiltered) result
for a match. Passing the ID directly skips that call entirely. This applies
both to working on something that already exists and to creating something
new:

- **Existing project/recipe/folder:** if the user gives you a Workato URL
  (`https://app.workato.com/recipes/<id>`, or a project/folder link), parse
  the ID straight out of the URL and pass it directly to
  `recipe_builder_init`/`recipe_get`/`folder_list` scoping — don't call
  `recipe_list`/`project_list` to "find" something you already have a link
  to. If the user only gives you a name, ask for the link or ID before
  proceeding rather than resolving by name search, especially when there
  could be more than one asset with that name (see the multi-copy gotcha in
  `known-bugs.md` — name-based resolution is also how you'd silently land
  on the wrong duplicate).
- **New recipe or project folder:** know the destination `project_id`/
  `folder_id` explicitly before creating — get it from a link the user
  gives you for the parent project/folder, or from an ID you already
  resolved earlier in the same task — rather than creating first and
  verifying placement after, or listing folders to locate a destination the
  user could have just linked you to directly.
- **Net effect:** an ID or link turns a potential list-and-scan round trip
  into a zero-cost parse of a string already in the conversation. When a
  name is all you have and the target matters (you're about to mutate or
  create something), a clarifying question for the link is usually cheaper
  than a broad list call.

## 3. Avoid redundant big-object fetches

- **Don't re-pull the same recipe repeatedly within a build.** Once you've
  called `recipe_builder_show`/`recipe_builder_pull` and have the current
  state in context, keep working from that until you push again. A push
  invalidates the session (see `known-bugs.md`), so the *only* time you
  need a fresh pull is right after a push — not "just to double-check"
  mid-edit when nothing has changed since your last read.
- **Don't call `recipe_builder_get_datapills` or `get_input_schema` more
  than once per step configuration.** Fetch the schema/pill list for a
  step once, then reuse it across every `set_input_field` call you make
  against that step in the same turn, instead of re-fetching before each
  individual field edit.
- **Don't call `recipe_builder_list_connector_operations` or
  `recipe_builder_search_connectors` broadly when you already know the
  action name.** A full operation list for a large connector can be huge;
  search with the specific keyword/action name instead of listing
  everything and scanning for it yourself.
- **Verify a specific field, not the whole recipe.** After confirming a
  push landed correctly, you often only need to check one filter, one
  mapping, or one scoping field — not read the entire returned DSL back
  into your reasoning. Look for the specific path and move on.

## 4. Narrow queries over broad ones

- **`get_records` with filters, not a full table dump.** Since
  `data_table_query` 404s in this environment anyway (see `known-bugs.md`),
  reading table contents already routes through a recipe's `get_records`
  step or `job_list` — use the narrowest filter that answers the actual
  question (a specific key, a date range) rather than pulling every row.
- **`job_list` with a filter/limit, not the full job history.** When
  investigating a recipe's behavior, scope to the relevant time window or
  status rather than listing every job the recipe has ever run.
- **`job_get` on the specific job/step in question.** A job trace can carry
  large nested payloads (bulk WQL results, big API responses per step) —
  don't pull a job's full trace when the question is about one step's
  input or error.
- **`knowledge_base_list_documents`, `collaborator_list`, `recipe_list`,
  `folder_list`:** use available filters (project/folder scoping, name
  match) instead of listing broadly and filtering client-side in your own
  reasoning — every unfiltered row costs tokens whether or not you use it.

## 5. Reuse session state instead of re-deriving it

- **Preserve and reuse IDs, handles, and URLs exactly as returned.** Once
  `asset_url_get`, `recipe_list`, or any `*_get` call has given you an ID,
  pass it directly into subsequent calls — don't re-list or re-search to
  "confirm" an ID you already have in context.
- **Verify-before-mutating pays for itself.** Reading an asset before
  editing it (per the non-negotiables) avoids a wasted
  edit-push-discover-wrong-target-redo cycle — each redo is a full
  additional round of large tool responses, which costs far more than the
  one extra `*_get` call up front.
- **Don't restart a session that's still valid.** `recipe_builder_init`/
  `genie_get` exist to seed a session once; reuse the same session for
  every subsequent call in the same logical task rather than re-seeding
  per step. (But keep `recipe_builder_*` and `genie_*` sessions on separate
  `session_id`s — see `known-bugs.md` for why mixing them backfires.)

## 6. Avoid validation-error round trips

Each failed tool call (a bad field name, a wrong input shape, a schema
mismatch) is not free — it's a full extra request/response cycle, and the
error payload itself can be verbose. Front-load the calls that prevent
these:

- Call `recipe_builder_get_input_schema`/`get_datapills` **before** guessing
  at a field's shape, rather than trial-and-error submitting values and
  reading back `InputFieldNotInSchema`/`DatapillInvalidPath` errors.
- When a step's shape is genuinely unknown (e.g. `declare_variable`'s
  dual schema — see `known-bugs.md`), fetch the schema fresh for that
  specific step rather than assuming it matches a previously-seen recipe's
  shape and finding out via a failed call.
- Plan a multi-step recipe's structure in a short design pass before
  building, rather than discovering the right sequence through repeated
  add-step/show/undo cycles — each cycle re-fetches the growing recipe.

## 7. Response hygiene — don't echo large payloads back to the user

A pulled recipe DSL, a full job trace, or a large data-table read does not
need to be reproduced verbatim in your own response just because you
fetched it. State what you confirmed or changed concisely (pass/fail, the
specific field and its value, the delta from before) rather than pasting
the full JSON back — the fetch already cost tokens once; restating it in
your own output doubles that cost for no added value to the user.

## 8. Delegate bulk exploration instead of looping inline

When a task requires touching many assets — auditing every recipe in a
folder for a given connector, checking a field's usage across a dozen
skills, migrating a pattern across several recipes — looping through
`recipe_get`/`recipe_list` calls one by one in the main conversation fills
the primary context with data that's only needed transiently. Prefer
handing that fan-out to a subagent (the `Explore` agent for read-only
surveys, `general-purpose` for multi-step work per asset) and having it
report back a synthesized result, keeping the bulk intermediate data out
of the main context entirely.

## 9. `docs_get` discipline

`docs_get(id="guides:overview")` (or any other guide page) is itself a
response of real size. Fetch a given guide once per session and treat it
as known for the rest of that session — don't re-fetch the same guide id
again later in the same task just because a new sub-question came up that
the already-fetched guide likely already covers.
