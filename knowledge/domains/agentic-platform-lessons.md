# Agentic platform lessons — code-grounded study

Lessons mined from a production agentic platform codebase (a large
multi-service agent platform) and mapped onto Workato no-code/low-code
development. Every lesson cites real code patterns. Three-way
attribution matters: some patterns are native to the platform, some
are automation-side inventions flowing INTO it, some are harness
ports of platform patterns — the traffic runs both directions, and
the meta-skill is the translation.

## How fast does verification have to be for agent-authored volume?

Verification throughput caps agent-authored volume. The platform's
per-change sandbox model (fork changed services + path closure,
template-cloned databases with a short TTL, additive-only changes) is
one answer. For a fixed end-customer workspace with no
workspace-per-change plane, that model is REJECTED — the
project-level fallback (prefixed tables + namespaced fixtures +
provisioner/reaper machinery) gives up more than it gains. The lesson
that DOES carry: keep verification cheap and unqueued (a dev
workspace + test cases + trace-evidence verification). Do not
re-propose workspace or project sandboxes.

## What does "delivered" actually mean?

"Delivered" = durably recorded, not enqueued. The platform acks an
event only when its marker is byte-verbatim in the on-disk
transcript, and a resume sweep re-drives acked-but-never-run items.
Mapping: done = a cited job trace or test run, never say-so.

## How should failure policy vary across components?

Per-component, not blanket. Judges differ by design: send/share
fail-open on rate limits and fail-closed otherwise; an approval
classifier fails closed to "ask" on every error; a schedule judge has
no approval path at all; an event router quarantines permanent
failures instead of retrying them. Mapping: classify every error
branch as transient/permanent/capacity in the development playbook
and the build checklist.

## What is the two-signal rule?

On an upstream outage, the user sees a soft-success "retry
scheduled" while the operator sees the real error verdict — exactly
one authoritative outcome per turn (an outcome latch). Mapping:
notification recipes are not monitoring; monitoring reads the run
outcome, never the notification message.

## When do you extract shared abstractions?

"Two is a coincidence, three is a pattern." The platform's gateway
layer refuses to abstract what two interfaces share but enforces what
all three share (a callback route, dedup-as-context-manager, "a 429
must stay a 429"). Mapping: extract shared functions/connections at
third use, not second; keep per-asset quirks local at the boundary.

## How should credentials survive rotation?

Rotation-safe credentials: webhook verification resolves current +
retiring tokens per webhook and tries each — rotation never 403s a
live webhook; the resolver never raises, and fail-closed lives at the
wiring layer. Mapping: accept old+new credentials in rotation
windows, resolve per connection, never a global secret.

## Why is cache-prefix discipline a correctness issue?

Sorted tool lists, hashed prompts in logs, byte-identical wording
across turns — the prompt prefix is both cache AND behavior.
Mapping: genie/skill instructions stay byte-stable across publishes;
no volatile state early in the instruction text.

## Which guardrails must the agent not be able to turn off?

Guardrails are not agent-configurable: a URL-sanitizer stop hook is
deliberately not a pluggable provider. Mapping: decide which knobs
the agent may turn vs which are constitutional (permission gates
layered above genie tools, unremovable).

## How do idempotency keys survive event redelivery and versioning?

Fold a version timestamp into dedup keys so one event's redeliveries
collapse but a later distinct change re-notifies. Mapping: reruns
dedupe on (entity, event, event-version).

## Where should state and retries live?

Lossy in-memory, durable at the edges: the tick queue drops at
capacity and counts it; durability lives in the database cursor +
safety net; retries exist at exactly one layer. No retry queue, no
DLQ. Mapping: put state/retry at one layer (the data table / the
ledger), keep inner steps disposable.

## Where must observability live relative to the observed?

Observability must not share a failure domain with the observed:
loop metrics are emitted by the loop, not derived from spans, so
loop death is observable when tracing is down. Mapping: the
monitoring path reads platform state directly, never the bot's own
announcements.

## What is an agentic platform, really?

Delivery + governance with an LLM in the middle. The worker loop is
thin — it delegates reasoning to the model CLI and spends its code
on exactly-once delivery, restart/resume, permission layers, and
invariants that survive dying mid-turn (open-turn markers, resume
sweeps, dedup-from-disk). Mapping: genies/skills/MCP servers are
easy; the engineering is what happens when everything dies at the
wrong moment. Design for that.

## What is the semantic-gateway modality?

A reviewed-capability gateway pattern: a capability registry with
content-free routing, typed facts instead of raw payloads, and
plan-then-confirm-commit for mutations. Born on the automation side
and imported into the platform as a phase-1 service (OAuth-chain
proof via a connector bridge — the service must 401 on an expired
upstream token, never self-refresh); later phases: execute/plan-
commit, MCP rewiring, packaging. It is the general shape for every
agent-facing surface.

## Which lessons are action items vs closed?

Change sandboxes — rejected (see the verification section; do not
re-propose). Open action items: failure classification, the
two-signal rule, rotation-safe connections, and the semantic-gateway
modality as the general shape for agent-facing surfaces.
