# Workflow app and MCP UI surfaces

Read this reference before editing Workflow app HTML/JavaScript, an MCP iframe
resource, long-running operation UX, or sandboxed link/copy behavior.

## Live-source and edit safety

- Treat the signed-in Workato editor as authoritative. Read and snapshot its
  complete current source and version immediately before mutation; compare any
  local copy before using it. A local file created during an earlier debugging
  session may be stale even when its filename says "live".
- Keep formatting-only work mechanically separate from behavior changes. After
  a browser edit, re-read the saved source and test the actual hosted App/MCP
  surface rather than relying on the editor's save toast.
- Preserve tested selectors, handler names, tool payloads, result schemas,
  idempotency keys, reset behavior, and accessible states unless the change
  contract explicitly includes them. Keep a complete pre-edit source snapshot
  as the rollback point.
- Render untrusted/user/CSV/provider strings with `textContent`, validate URLs
  against an allowlist, and avoid assigning those values through `innerHTML`.

## Resumable long-running operations

- An iframe or chat surface can be unmounted when the user changes tabs or
  conversations. Persist only the safe resumable state keyed by a stable
  operation/session ID and treat the backend ledger/status endpoint as truth.
  Do not persist raw CSV rows or lead-level PII longer than the governed upload
  lifecycle requires.
- On remount, restore the selected operation, resume bounded polling, and
  rehydrate final counts from the terminal backend record. Never display zero
  merely because in-memory counters were lost.
- Drive progress from real backend stages/chunk counts. Smooth visual movement
  between observed checkpoints, cap optimistic progress below completion, and
  move to 100% only on a terminal success record.
- Every terminal backend error needs an immediate user-visible failure state,
  correlation/job identifier, safe retry or admin guidance, and an end to
  polling. Add a timeout/manual-refresh path so missing callbacks cannot strand
  the user indefinitely.

## URLs and clipboard behavior in sandboxes

- Assume iframe navigation or pop-outs may be blocked. Every surfaced URL must
  display the complete copyable address and a clear **Copy** action; do not tell
  users a link will open unless the host permits and testing proves it.
- Use the async Clipboard API when available, then a hidden selectable input or
  `execCommand('copy')` fallback, and finally select the visible URL with clear
  manual-copy guidance. Report copied/failed state accessibly without losing
  the URL.
- Generate canonical URLs from authoritative platform IDs, including required
  prefixes/suffixes that a numeric REST ID alone does not contain. Validate the
  final URL and preserve it in backend state so remounts do not reconstruct a
  different address.
