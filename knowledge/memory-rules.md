# Memory authoring rules

Follow these rules for every file in this knowledge tree — they are what makes
the fractal layer index your content correctly.

- One file one topic: the H1 is the factlet title.
- Sections are `## `-delimited and written AS queries — headings are the
  factlet API.
- Structured facts go in the claims ledger, not prose.
- Every file has an INDEX line in `INDEX.md`.
- Frontmatter values are quoted.

How you interact with memory does not change: files stay the source of truth
(human-editable, git-versioned); the fractal layer is a projection rebuilt
incrementally on read. The read path prefers zoom — never write into SQLite.
