# MCP server delivery

Use this reference for Workato MCP tool contracts, inventory changes, client
rediscovery, compatibility, UAT, benchmarking, and release readiness.

## Contract and routing

- Treat Verified User Access as the standing identity model for interactive
  Workato MCP tools unless the project explicitly defines another model. Design
  and test every path as the calling user; do not repeatedly present that
  baseline as a special feature.
- Optimize for task-complete, bounded responses. Return the fields an agent needs
  to choose or complete its next action, omit redundant provider payloads, and
  expose bounded detail or pagination when more data is legitimately required.
- Tool descriptions are both routing contracts and recurring context cost.
  Deduplicate them, state when to use the tool, point normal workflows to the
  preferred path, and identify legacy tools as backward compatibility only.
- Verify rendered tool-card copy as well as the model-facing contract. Keep the
  visible leading descriptor to one task-complete sentence; place necessary
  operational detail in the structured help/contract rather than repeating it
  in the card body.
- Preserve legacy contracts until usage evidence and an explicit decision support
  deprecation or removal. Test preferred workflows without legacy tools, then run
  compatibility tests separately.

## Privileged provider isolation

If a scheduled refresh or indexing workflow requires a privileged provider
service connection, isolate that connection to the refresh path and its
explicitly owned assets. The interactive MCP request path may query a
purpose-built, fixed-field materialized read model, but must not contain,
inherit, or dynamically invoke the privileged provider connection. Keep the
public caller identity model explicit and enforce closed inputs plus a fixed
output-field allowlist.

Prove this boundary structurally from the live recipe graph, selected
connections, and runtime adapters, and behaviorally with adversarial identity,
prompt-injection, closed-enum, and out-of-scope-field tests. Record structural
and behavioral evidence separately; connector absence in an export or a green
parent job alone is not sufficient proof.

## Inventory and registration

- Record the authoritative Workato server ID, connected-app registration, local
  endpoint, environment, tool namespace, and exact expected names/count. These
  are different layers and may expose different cached snapshots.
- After attach, detach, rename, or replacement, reconnect or start a fresh client
  session, list tools, compare exact names/count, and call one safe read-only tool.
  A UI refresh alone is not client rediscovery.
- Treat an unexpected suffix such as `_2`, an old description, a mismatched count,
  or `tools not supported` as unresolved registration/cache evidence. Resolve by
  asset ID and endpoint before declaring the inventory live.

## UAT and benchmarking

Use an escalating test ladder:

1. Schema and mapping checks, including optional, empty, HTML, multipart, and
   boundary inputs relevant to the public contract.
2. A structural UI gate for every changed tool: no unresolved mapping, formula,
   or datapill errors; each provider call is inside the intended error boundary;
   and handled failures return the documented structured contract without raw
   provider exceptions.
3. Normal UAT for every tool and preferred multi-tool workflow.
4. Regression tests for unchanged functionality and legacy compatibility.
5. Adversarial security and identity tests with safe fixtures.
6. Bounded pressure/load tests and final conversational end-to-end scenarios.
7. Fresh-session reconnect and rediscovery validation.

Automated fixtures can encode the implementation's own assumptions. Include
independent real-world or surprise fixtures and verify the user-visible result,
not merely a green job or schema-valid response.

For performance claims, compare like-for-like callers, fixtures, limits,
promoted data versions, and task outcomes. Warm each path, repeat runs, report
p50/p95 and errors, and measure complete workflows as well as isolated calls.
Capture client-to-tool-start, tool execution/return, and final-answer completion
when user-facing latency matters; separate client wall-clock time from Workato
job/step time. Attribute gains to the changed layer rather than treating a token
reduction, refresh/build duration, or isolated backend result as an end-to-end
improvement. Label deterministic character-based token estimates separately
from provider-measured tokens. Report tool-output payload tokens separately from
request/model-context tokens; never present an input or context reduction as an
output-payload saving.

Load tests must report concurrency, success/error semantics, completeness,
answer uniqueness, snapshot/version integrity, and recovery after overload.
Freeze refresh promotion for strict same-version comparisons, or segment results
by version. A burst may legitimately span an atomic promotion and observe
different versions across separate requests; each request must still resolve
one complete internally consistent version. Treat a client timeout as a
saturation claim only after checking Workato queue/job evidence and attempting a
bounded reproduction. Increase concurrency only when evidence identifies the
bottleneck. Re-benchmark after adding telemetry or changing routing descriptions.

When an MCP change adds a compact/detail, pagination, or projection control,
extend the regression harness to exercise the exact alternate mode. Compare it
to the compatibility path against the same caller, fixture, and limits; record
response size/token proxy, p50/p95, errors, and task completion. Do not claim a
token-only reduction as a latency or workflow improvement, and stop a long tail
of tiny contract-text edits once their measured gain no longer justifies the
lifecycle and regression risk.

## Release and cutover

Develop high-risk connector, authentication, URL, or concurrency changes beside
the proven server when an isolated experimental path materially lowers risk.
Preserve rollback and prefer an in-place cutover on the established endpoint
when compatibility is proven; use a new endpoint only when the contract cannot
be preserved safely.

Package exactly the changed Workato assets and required dependencies through
Recipe Lifecycle Management, not a generic deploy action. Exclude `zArchive`,
test harnesses, and unchanged folder peers unless intentionally required. After
import, verify environment mappings, folder placement, server inventory, fresh
client discovery, and a bounded Production smoke test before declaring rollout.
When selecting an MCP server expands attached skills or dependencies, reconcile
the expanded set against actual change scope and resolve every deselection or
dependency warning before release approval.
