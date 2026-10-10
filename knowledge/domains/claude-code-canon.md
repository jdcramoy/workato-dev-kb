# Claude Code docs canon — official best practices for knowledge, memory, skills, file trees

The rulebook for auditing instruction files, auto memory, skills, and
settings layout. Every rule is from OFFICIAL documentation (sources at
the bottom). Where a local harness deliberately diverges, the
divergence is stated — the harness policy wins, never the default.

## How should instruction files be sized and structured?

CLAUDE.md files load from the working directory and every directory
above it, concatenated root-down (closer to the working directory
loads last, reads last). Subdirectory CLAUDE.md files load on demand
when file operations touch files there. `@path` imports expand at
launch (max depth 4 hops; imports do NOT save context — they all
load). Block-level HTML comments are stripped from context (use them
for human-only notes).

- Size: target under 200 lines per CLAUDE.md file. Longer = more
  context consumed AND measurably lower adherence. Move single-topic
  content to path-scoped rules.
- Concrete over vague: "Use 2-space indentation", not "format
  properly". Instructions the model can verify follow reliably; vague
  ones don't.
- Contradictions are poison: if two instruction files conflict, the
  model may pick either arbitrarily. Cross-file consistency is an
  audit item.
- `.claude/rules/`: one topic per file; `paths:` frontmatter scopes a
  rule to file globs (loads only on matching file operations).
  User-level rules load before project rules; user vs project
  conflicts resolve arbitrarily — keep them consistent.
- CLAUDE.md is context, not enforcement: anything that MUST happen at
  a lifecycle point (pre-commit, post-edit) belongs in a hook, not a
  rule. Settings (`permissions.deny` etc.) are client-enforced;
  CLAUDE.md is not.
- `/doctor prompt-audit` audits all instruction surfaces for outdated
  refs, dead file paths, cross-file contradictions; `/doctor` trims
  code-derivable content while keeping pitfalls, rationale, and
  non-default conventions.
- Audit hooks: files over 200 lines; instructions restating what the
  code shows; dead `@path`/file references; cross-file
  contradictions; multi-step procedures living in CLAUDE.md that
  belong in a skill; "always do X at moment Y" rules that belong in a
  hook.

## What are the auto-memory rules and limits?

Auto memory is per-project (derived from the git repo; all
worktrees/subdirs share one directory), machine-local, never synced.
MEMORY.md is the index — the first 200 lines or 25KB loads at session
start, whichever comes first; the rest is silently dropped. Topic
files are NOT loaded at startup — read on demand. Claude Code stamps
a `modified` ISO-8601 frontmatter field on writes.

- Type taxonomy is closed: `user` (role, expertise, preferences),
  `feedback` (corrections + confirmed approaches), `project` (ongoing
  work, deadlines, decisions not derivable from code/git),
  `reference` (pointers outside the project). Memory type = the
  frontmatter `type` field.
- Skip rule: never save what is derivable from the codebase
  (architecture, file paths, past fixes) or already stated in
  CLAUDE.md.
- Index hygiene: one line per memory in MEMORY.md; detail lives in
  topic files; the harness warns near the limit and errors (rewrite
  required) when over it, because overflow is dropped on next load.
- Audit hooks: index line count + byte size vs the caps; entries
  whose detail leaked into the index instead of a topic file; wrong
  or missing `type`; stale entries (cross-check `modified`); memory
  content duplicated in a knowledge file (and vice versa);
  derivable-from-repo content that should never have been saved.

## What are the skill-authoring rules from the official spec?

Three-tier progressive disclosure: (1) metadata (~100 tokens)
pre-loaded for EVERY skill; (2) SKILL.md body loaded on activation
(under 500 lines / 5000 tokens recommended); (3) bundled files loaded
or executed only on demand. The description is the routing surface —
the model selects from 100+ skills by description alone.

- Frontmatter rules: `name` ≤64 chars, lowercase letters/numbers/
  hyphens only, no leading/trailing/double hyphens, no reserved
  vendor words, MUST match the parent directory name. `description`
  ≤1024 chars, non-empty, no XML tags. Optional: `license`,
  `compatibility` (≤500 chars, only when env requirements exist),
  `metadata` (string→string map), `allowed-tools` (experimental).
- Description = what + when, third person, with concrete trigger
  terms ("Use when..."). Vague descriptions cause missed or wrong
  activation — the #1 discoverability failure.
- Naming: gerund form preferred (`processing-pdfs`); avoid vague
  (`helper`, `utils`) and over-generic (`files`, `data`) names.
- Conciseness: assume the model is smart; only add context it doesn't
  have. Cut explanations of well-known concepts.
- Degrees of freedom: high freedom (prose guidance) for
  context-dependent work; low freedom (exact scripts, "do not
  modify") for fragile/critical sequences. Match specificity to
  fragility.
- References sit one level deep from SKILL.md (deeper chains get
  previewed with `head`, not fully read). Reference files over 100
  lines get a table of contents. No deeply nested reference chains.
- No time-sensitive content ("before August 2025 use X") — use an
  "old patterns" collapsible section instead. Consistent terminology
  throughout.
- Scripts: solve, don't defer (handle errors in the script; don't
  fail and let the model improvise). No voodoo constants — every
  value justified. State explicitly whether the script should be
  EXECUTED or READ as reference. Prefer scripts for deterministic
  work (reliable, token-free until output).
- MCP tools in skills: fully qualified `ServerName:tool_name` — bare
  names fail to resolve with multiple servers installed.
- Evaluation-first: build 3+ evaluation scenarios BEFORE writing
  docs; measure the baseline without the skill; iterate author-model
  vs user-model sessions on real tasks.
- Audit hooks: every SKILL.md vs this checklist (name/dir match,
  description quality, line count, one-level refs, stale
  date-sensitive content, reserved words). Prioritize by the usage
  ledger — high-traffic skills get audited hardest. Validate
  frontmatter mechanically where possible.

## How do settings scopes and precedence work?

Four scopes, highest precedence first: managed → command line →
project local (`.claude/settings.local.json`) → shared project
(`.claude/settings.json`) → user (`~/.claude/settings.json`). Higher
settings win per key. List keys (permissions.allow etc.) MERGE across
scopes instead of overriding. A few security keys honor the stricter
value from any scope.

- Settings are strict JSON — no comments, no trailing commas; a
  broken file means a Settings Error at startup. `~/.claude.json` is
  Claude-managed state (never hand-edit; backups in
  `~/.claude/backups/`).
- Repo-committed keys need folder trust before they apply;
  `deny`/`ask` apply immediately, `allow` waits for trust. Some keys
  NEVER apply from a repo file (scoped in the settings index).
- `/status` shows which sources loaded; `claude doctor` lists
  rejected entries. Settings edits hot-reload mid-session (except a
  few session-start-only keys like `model` — use `/model`).
- Audit hooks: the same key set in conflicting scopes; permissions
  sprawl in the wrong file (personal rules in shared files, team
  rules in local files); dead allow/deny patterns; invalid JSON.

## How should a harness document its deliberate divergences?

A local harness may deliberately diverge from these defaults — the
pattern to copy is DOCUMENTING the divergence so it is never silently
reverted to the default. Typical deliberate divergences: a dedicated
knowledge TREE as the durable store (not bloated instruction files —
audit the tree with these rules but never propose merging it into
CLAUDE.md); auto-memory kept as a thin index INTO that tree; a claims
ledger superseding plain memory for durable platform facts (claims
carry evidence + confidence + last_verified, audited on a cadence);
write-lint hooks enforcing frontmatter and index-touch mechanically.

## Sources (official)

- Memory / CLAUDE.md / auto memory: https://code.claude.com/docs/en/memory
- Skill authoring best practices: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- Agent Skills specification: https://agentskills.io/specification
- Settings files and precedence: https://code.claude.com/docs/en/settings
