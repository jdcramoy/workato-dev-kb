---
name: workato-kb
description: Deep Workato knowledge base (RAG++) over docs.workato.com, the product changelog, the Workato blog/The Connector, architecture-canon GitHub repos (system-design-primer, awesome-software-architecture, awesome-system-design-resources), Enterprise Integration Patterns, and Claude platform docs. Use during planning, scoping, design, build, and test phases of ANY Workato work, when a Workato platform question could be answered from documentation, when a formula/connector/best-practice detail is needed, when designing system architecture, or when citing docs. Daily companion during planning and build.
---

# Workato KB (RAG++)

A local, nightly-synced knowledge base. Seven sources, one index, mandatory
citations.

| Source | What it holds | Privacy |
|---|---|---|
| `docs` | All docs.workato.com pages as Markdown (Gears, frameworks, best practices, formulas, connectors, SDK, AIRO, Agent Studio...) | public |
| `changelog` | product-hub changelog posts (releases, deprecations, new connectors) | public |
| `blog` | Workato blog + The Connector posts (thought leadership, launch announcements, patterns) | public |
| `github` | Architecture canon: system-design-primer, awesome-software-architecture, awesome-system-design-resources (shallow-cloned, git-pull sync) | public |
| `eip` | Enterprise Integration Patterns (every messaging pattern + real-world examples) | public |
| `claude` | Claude platform docs (llms.txt markdown twins) | public |
| `inspiration` | Workato Automation Inspiration (real-world automation pattern posts) | public |

## The retrieval loop (agentic — do NOT dump results into context)

**Fast path (preferred):** if `mcp__workato-kb__*` tools are in your tool list,
use them — `kb_search` / `kb_read` / `kb_zoom` — the server is resident and the
embedding model is already warm (~0.17s/query vs ~9s CLI cold). The CLI below
is the fallback for when the MCP server isn't connected.

1. **Query**: from the repo checkout, `.venv/bin/python -m kb.search "your question" [-k 8] [--source docs] [--section Connectors]`.
   - Phase-2 stack: BM25 + dense vector + RRF fusion + cross-encoder rerank, then entropy modulation (surprise density lifts distinctive chunks) and PMI (query-term concentration), then a retrieval-distribution confidence gate — weak+flat results trigger one expanded second round automatically (reported in the output header).
   - Ablate with `--no-entropy` / `--no-pmi` when comparing result quality.
   - `--source` narrows: `docs|changelog|blog|inspiration|github|eip|claude`.
2. **Skim and refine**: snippets tell you if the hits are on target. Refine the query or add a filter before reading anything in full. Two refinements max, then widen.
3. **Read**: `.venv/bin/python -m kb.read <doc_id>` or `.venv/bin/python -m kb.read --url "..."` for the full document. Read only what you need.
4. **Cite every claim**: canonical URL from the hit.
5. **Freshness guard**: if a page might be stale (recent release, deprecation), fetch the live page — docs pages accept `.md` suffix (e.g. `https://docs.workato.com/en/airo.md` returns raw Markdown); use an MCP web-fetch tool for others. If live and local disagree, the live page wins; report the divergence.

## Corpus maintenance

- Nightly sync (cron/launchd job): etag-conditional docs sync, changelog RSS,
  blog pagination, re-index of changed docs, and a daily diff report.
- Re-run the index manually after a corpus change: `.venv/bin/python -m kb.indexer`. Also rebuild term stats + entropy: `.venv/bin/python -m kb.pmi build && .venv/bin/python -m kb.entropy score` (search.py auto-builds term stats if missing).
- Phase-2 layers live in `kb/search.py`: entropy modulation (ENTROPY_WEIGHT 0.15), PMI (PMI_WEIGHT 0.10, term stats in `index/terms.json.gz`), confidence gate (expand when distribution entropy > 0.9 bits AND top rerank < 0.5). Known entropy edge: proper-noun lists score high on raw rarity — that's why entropy only modulates rerank-gated candidates.

## Advanced modes

- `kb zoom "topic"` — fractal zoom: coarse-first retrieval over the factlet
  hierarchy (topic → file → section → chunk); read the coarsest level that
  answers, drill only on decision. The factlet schema is identical at every
  scale — same unit, any depth.
- `kb funnel search "query"` — Matryoshka-style funnel: coarse page-context
  embedding shortlists, fine chunk search refines. Falls back to plain fine
  search when the coarse layer's coverage is weak. Coverage grows as the
  contextualize backlog drains.
- MCP server: `.venv/bin/python -m kb.mcp_server`
  (stdio; kb_search / kb_read / kb_zoom) for any MCP client.

## Known limits

- Attachments (PDFs) not crawled.
- bge-base embeddings + bge-reranker-base are local and good, not frontier. If keyword recall fails on a fuzzy concept question, try synonyms via the query-expansion table in `kb/search.py` or rephrase.
- The changelog backfill is archive-pagination based; if Workato changes the archive structure, backfill via `python3 -m kb.wp_source`-style scripts with the feed as fallback.
