# Workato Dev KB — knowledge index

One line per file. Read the line, then the file.

## Domains
- workato-platform-traps.md — every generalizable Workato trap: recipe-builder push/validation, data tables, Ruby sandbox, functions vs skills, testing, manifests, MCP tools, scheduler, WQL, data-correctness patterns
- agentic-platform-lessons.md — code-grounded agent-platform lessons mapped to Workato: verification throughput, delivery semantics, failure policy, idempotency, observability, the semantic-gateway modality
- mcp-operations.md — MCP client/server lifecycle: reconnect vs re-consent, mcp-remote races, Workato-hosted MCP auth, claude.ai connector token costs, tool-not-found and Connected-UI diagnosis
- architecture-patterns.md — per-project two-diagram convention (system map + asset graph), node citations, nightly drift-QA loop, tracker-verifies-nodes-not-edges
- claude-code-canon.md — official Claude Code best practices for instruction files, auto memory, skills, and settings precedence, with audit hooks for each
- slack-bot-connections.md — the governed Slack-bot SOP: workspace-owned app, net-new OAuth profile, manifest scopes, api.slack.com automation traps, silent-no-delivery genie triage

## Skills (in this pack under skills/)
- airo-mcp-playbook — AIRO MCP trap catalog: silent-failure bugs, push/validation traps, efficiency practices
- workato-kb — query the pack's RAG++ corpus; retrieval modes, freshness guard, citation rules
- workato-development-playbook — the cross-project operating model: inventory, design, build, audit, test, governed release
- verify — live-evidence-before-done gate: pass/fail tables citing job traces, never claims
- diff-reading — separate behavioral changes from cosmetic ones in recipe DSL diffs

## Rules
- memory-rules.md — how to author files here so the fractal layer indexes them (one file one topic, ## sections written AS queries, INDEX line per file)
