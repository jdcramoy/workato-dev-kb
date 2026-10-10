---
name: diff-reading
description: Read a Workato recipe DSL diff and separate behavioral changes from cosmetic ones — what actually changed in execution terms. Use when reviewing a recipe_builder diff, a version diff, a snapshot-history diff, or before approving any push based on a diff.
---

# Diff reading

A diff is a behavioral claim, not formatting. Read it in execution terms.

## The separation

**Cosmetic (usually safe):** step renames, comment text, indentation/order
shifts with identical code, UI-only layout fields, description edits.

**Behavioral (always matters):** input field values, datapill references,
condition expressions, loop sources, error-handling boundaries, connection
bindings, masks, schedule cadence, trigger filters, formula text.

**Hidden-behavioral (the dangerous class):**
- A datapill that renders as its sample value in the DSL export (the known
  trap) — the diff looks like a literal change but may be a rendering artifact
- Connection changes that only show as an ID swap
- Step reorder inside a conditional block (order IS behavior there)

## Procedure

1. Read the diff twice: once for the summary (what sections moved), once
   step-by-step for input/condition/connection lines.
2. Every changed line gets a verdict: cosmetic / behavioral / unclear.
3. "Unclear" is not "probably fine" — verify it against the UI or a job
   trace before sign-off.
4. Report as: behavioral changes listed with their execution meaning;
   cosmetics counted, not enumerated; unclear items named and blocking.

## Rules

- Never approve a push from a diff you haven't classified line by line.
- DSL exports may show sample values as literals; distrust mapped-looking
  constants (the datapill trap).
- A recipe snapshot history is the baseline for "what changed since
  baseline" questions.
