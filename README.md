# workato-dev-kb

A pre-baked, installable knowledge pack for Workato developers using Claude
Code. It ships the RAG++ retrieval stack (hybrid BM25 + dense + rerank +
entropy/PMI scoring), the fractal memory layer (self-similar factlets at
topic/file/section/chunk scale with zoom retrieval), and a curated set of
generic-form playbooks and trap catalogs — all over public Workato sources.

## Quickstart

```bash
git clone git@github.com:Workato-All/workato-dev-kb.git && cd workato-dev-kb
./install.sh
kb "how do I handle pagination in a custom connector"
```

The installer checks prerequisites (and offers to fix them), builds a
python3.12 venv, downloads the embedding models, syncs the public sources,
builds the index and fractal projection, and runs a smoke query to prove your
first search works. It is safe to re-run at any point.

### Workato-All teammates: one command gets everything

```bash
./install.sh --with-corpus
```

`--with-corpus` also installs the team knowledge corpus
([Workato-All/knowledge-corpus](https://github.com/Workato-All/knowledge-corpus),
the sanitized team-shared knowledge tree) into `~/.claude/knowledge`, where
the fractal layer indexes it — so the memory layer answers from real build
doctrine, not just the scaffold. An existing knowledge tree is never
overwritten: a corpus clone gets updated in place, a personal tree is left
untouched. The corpus repo is internal-only; without access the installer
degrades gracefully and continues without it.

## Sources

| Source | What it holds |
|---|---|
| `docs` | docs.workato.com (first-party `llms.txt`, raw-Markdown twins) |
| `changelog` | product-hub changelog |
| `blog` | Workato blog + The Connector |
| `inspiration` | Automation Inspiration posts |
| `github` | curated architecture-canon repos |
| `eip` | enterpriseintegrationpatterns.com |
| `claude` | Claude platform docs |

Source enablement lives in `sources/public.json`.

## What is deliberately not here

Content from internal team wikis is excluded by design; if a wiki tier ships
later it will be a separate opt-in addition. Everything in `knowledge/` is
curated and written in generic form — this pack is not a mirror of any
internal workspace or wiki.
