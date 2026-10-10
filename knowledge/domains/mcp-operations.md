# MCP operations — client, server, and connection lifecycle traps

Cross-project MCP server/client operations knowledge: lifecycle
semantics, hosting patterns, token costs, and diagnosis playbooks.

## What does an MCP reconnect actually do?

A client `/mcp` reconnect is a CLIENT-side reconnection: it reuses
existing OAuth tokens and never re-runs a provider consent flow. Any
fix that requires NEW scopes needs the underlying connection revoked
and re-consented through the provider's real consent screen.

## How do duplicate mcp-remote processes cause auth storms?

MCP HTTP servers bridged via mcp-remote can duplicate processes when
the same URL is registered more than once; duplicates race rotating
OAuth refresh tokens and cause repeated interactive auth prompts. Pin
the mcp-remote version, run one process per URL, and measure cold
OAuth separately from warm tool latency.

## How are Workato-hosted MCP servers authorized and gated?

- Workato-hosted MCP servers use the Workato identity as the tester
  identity; access is gated by Workato user groups on the server
  asset.
- Per-caller verified-user connections mean each tester consents
  their own provider account (e.g. Google) on first connect.
- Server assets live per environment (Development vs Production).
- The public Workato MCP marketplace registry is separate from
  workspace-internal MCP servers — registry IDs are not workspace
  asset IDs.

## What are the token costs of claude.ai enterprise connectors in Claude Code?

From a large-org rollout thread (75 org connectors, ~29k tokens of
bare-repo context complaint), docs-verified and locally reproduced:

- On first-party Claude, tool search defers MCP tool definitions by
  default: only tool NAMES + server instructions load at session
  start. The residual MCP tax is the deferred-name list (~3.5k tokens
  for ~190 names, mostly authenticate stubs for unconnected servers)
  — skill descriptions cost MORE (~6.5k for ~35 skills). Org
  skill-catalog curation is the bigger lever than connector pruning.
- Per-user surgical controls (all verified): `deniedMcpServers` with
  serverName matching the connector display name blocks individual
  claude.ai connectors; `/mcp` toggles per project; users can
  disconnect individual org connectors from their own account.
  `enabledMcpjsonServers`/`disabledMcpjsonServers` apply ONLY to
  `.mcp.json` servers, not connectors. `disableClaudeAiConnectors`
  and `syncClaudeAiSkills:false` are the all-or-nothing flags (they
  kill wanted connectors along with the junk).
- serverName "*" wildcard does NOT work in `deniedMcpServers` —
  enumerate names (wildcards only on serverUrl/serverCommand).
- Admin side: per-tool ask/blocked controls exist per connector
  (blocked tools never enter the tool list); there is NO documented
  per-group/per-role connector assignment — provisioning is org-wide.
  Whether owner removal of an org connector disconnects members who
  already connected is NOT documented — verify empirically before
  promising it. Managed settings are a separate delivery path that
  claude.ai deletion never touches.
- Self-serve tip that landed well: tell Claude which connectors to
  keep, have it diff against the full set and write the
  deniedMcpServers block.

## Why does Claude refuse to use available MCP connectors?

With Advanced Research enabled in a claude.ai conversation, the model
delegates nearly every request to the research agent instead of
calling available Workato MCP connectors directly. When Claude
refuses to use available Workato MCP tools, Research routing is a
candidate root cause to check FIRST.

- Local vs remote matters: Research can auto-invoke remote MCP
  connector tools but NOT local-MCP-server tools — how a connector is
  registered (remote web MCP vs local desktop extension) decides
  whether delegation can succeed at all. Never assume locality
  without evidence.
- Do not prescribe "just turn off the Research toggle": the toggle
  may not be findable in the tools menu (stale state / UI defect /
  rollout gap all possible). Confirm the control exists before
  calling it actionable.
- Evidence discipline: shared conversation snapshots hide image
  attachments and omit tool-call traces, tool inventories, connector
  permissions, and errors; the assistant's prose about its own
  constraints is not proof of runtime state. Diagnose with a
  controlled read-only reproduction: record visible
  model/Research/connector state → one read-only MCP call → repeat
  outside the Project → toggle Research and compare.

## Why does "tool not found" mean there will be no job?

"Tool not found" = dispatch-level failure — no Workato job will
exist. Check the server audit chronology for tool renames/activation
dates: a wrapper calling a retired tool name while the registered
tool was renamed produces an MCP server error + zero jobs = contract
mismatch, not a backend outage.

## Why does a stale client catalog break benchmarking?

An advertised MCP catalog can diverge from the runtime surface —
"tool not found"/"Unknown tool" before schema validation = stale
client catalog vs server runtime. Fix is reconnect/refresh, then
confirm ONE write reaches schema validation before benchmarking.
Confirm endpoint identity before comparisons (a version alias is not
the production baseline).

## Why does a "Connected" UI status not mean execution works?

A runtime-connections UI showing "Connected" is not execution-time
authorization: a per-user VUA OAuth binding can fail at run time
("connection is broken. Please refresh") while the UI still shows
Connected. Recipe-level history + one failed job with the
authorization error localizes it. Diagnosis: a user-specific
re-auth — NEVER a shared-connection mutation.

## Which custom-connector and VUA traps recur?

- VUA runtime connections PIN a connector revision: releasing a new
  custom-connector version does NOT refresh existing caller
  connections — end users must re-connect (no in-place version
  refresh exists, no registry/recipe workaround).
- VUA classification is not inferred from using a VUA-capable
  connection: the skill attachment metadata must say VUA Required. A
  direct-array attachment stayed classified non-VUA even after
  remove/re-add.
- Password-type connector fields (e.g. client_secret) are STRIPPED
  from runtime user connections unless the field sets
  `preserve_in_runtime_connection: true` — the symptom is
  "connections currently unavailable" with no job created.
  Re-consent (not recreation) picks up the fix; the tradeoff is the
  secret copied into every runtime connection.
- Every automated push to a VUA-backed recipe can wipe the
  personalization flag from connection config — re-check after every
  push (a targeted single-recipe package re-import is the repair).
- MCP isError gaps and custom-connector runtime limits are covered
  in workato-platform-traps.md.
