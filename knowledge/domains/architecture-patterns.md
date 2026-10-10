# Architecture patterns — per-project diagram lifecycle

The convention for keeping two-level architecture diagrams per
automation project, plus the QA loop that keeps them honest. Two
mermaid files per project: a system map (systems/flows/triggers) and
an asset graph (platform assets + edges), both rendered to SVG via a
mermaid CLI render (the render IS the syntax check; the first run
downloads a browser and takes ~30s, later runs are faster).

## What does the diagram convention require?

- Two files per project in a per-project architecture folder:
  `system-map.mmd` (systems, flows, triggers) + `asset-graph.mmd`
  (platform assets + edges).
- Node citations are mandatory: each node cites its source line
  (`src: knowledge-line; tracker`) so a reader can re-derive it.
- Partial families are declared explicitly as REPRESENTED/OMITTED —
  never silently truncated.
- Diagram updates are triggered automatically by the session-handoff
  flow whenever platform/repo assets changed (and are manually
  invocable). Publishing an SVG to a wiki stays draft-then-approve
  (outward-facing).

## How does the QA loop catch diagram drift?

A nightly per-project review lens: drift-check the asset graph
against the live dependency tracker (the diagram is usually the wrong
one — verify with a platform read-back before claiming platform
drift), run a few best-practice queries against the knowledge base
with cited doc URLs, and file findings through the normal
dissent-gated proposal pipeline.

## What did the before/after test prove?

- RED: a fresh agent without the convention, given a cold "document
  the architecture" ask, produces good instincts (it chose mermaid)
  but an ad-hoc home, no asset graph, no tracker citations, no
  trigger, no QA loop.
- GREEN: a fresh agent WITH the convention produced both diagrams,
  every node cited, both rendered clean — and caught real docs rot
  (a project folder reference that had changed; tracker-verified the
  same night).

## What can the dependency tracker verify — nodes or edges?

The tracker verifies NODES, not EDGES (a dependency search returns no
caller→callee rows). Edge truth = the knowledge file, or a direct
platform read-back for a single critical edge (that read floods
context at ~226 KB per recipe — use it deliberately). Tracker node
data outranks the knowledge file when they disagree.

## How does the mermaid traps ledger work?

Mermaid syntax rules live in a SELF-TRAINING drafting skill with a
traps ledger. Loop: read traps → draft → render (the render is the
test) → on ANY failure or visual defect, append the trap (discovery
IS recording). Visual PNG inspection is mandatory. A periodic
consolidation pass merges the ledger. Proven traps include: no bare
`%%` comment lines (phantom nodes).

## Where do diagrams live, and what starts a project?

The pilot project proves the loop; every other project starts at "no
diagram yet" — created fresh at the first handoff that touches its
assets. Repo copies (docs/architecture/) exist only when a canonical
repo exists. Architecture docs never live in external workspaces —
if found there, MOVE them into the knowledge tree and leave a pointer
behind.
