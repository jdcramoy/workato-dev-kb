#!/usr/bin/env bash
# verify.sh — post-install doctor for workato-dev-kb.
# Read-only: reports PASS / WARN / FAIL per layer and exits non-zero only on
# hard failures. Run after ./install.sh, and any time something feels off.

set -uo pipefail
cd "$(dirname "$0")"

PASS=0; WARN=0; FAIL=0
row() { # row <status> <check> <detail>
  local icon; case "$1" in PASS) icon="✓"; PASS=$((PASS+1));; WARN) icon="~"; WARN=$((WARN+1));; *) icon="✗"; FAIL=$((FAIL+1));; esac
  printf "  %s %-4s %-28s %s\n" "$icon" "$1" "$2" "$3"
}

echo "== workato-dev-kb doctor =="

# --- 1. prereqs ---------------------------------------------------------------
command -v git >/dev/null && row PASS "git" "$(git --version | cut -d' ' -f1-3)" || row FAIL "git" "not found (brew install git)"
command -v jq >/dev/null && row PASS "jq" "$(jq --version 2>&1)" || row WARN "jq" "not found (brew install jq)"
command -v python3.12 >/dev/null && row PASS "python3.12" "$(python3.12 -V 2>&1)" || row FAIL "python3.12" "not found (brew install python@3.12)"

# --- 2. config ----------------------------------------------------------------
if [ -f config.sh ]; then
  # shellcheck disable=SC1091
  source config.sh
  : "${KB_INSTALL_DIR:=$HOME/.claude/workato-kb}"
  [ -n "${KB_INSTALL_DIR:-}" ] && row PASS "config.sh" "KB_INSTALL_DIR=${KB_INSTALL_DIR}" || row FAIL "config.sh" "KB_INSTALL_DIR empty"
  REPO_DIR="$(pwd)"
  case "${KB_INSTALL_DIR%/}" in
    "$REPO_DIR"|"$REPO_DIR"/*) row WARN "install dir" "inside the repo checkout — corpus data in a git clone risks accidental commits; move it out and update config.sh" ;;
    *) row PASS "install dir" "outside the checkout" ;;
  esac
  [ -n "${KB_PORT:-}" ] && row PASS "daemon port" "KB_PORT=${KB_PORT}" || row WARN "daemon port" "KB_PORT unset (daemon optional)"
else
  row FAIL "config.sh" "missing — copy config.example.sh config.sh (or re-run ./install.sh)"
fi

# --- 3. venv -------------------------------------------------------------------
if [ -x ".venv/bin/python" ]; then
  VPY=$(.venv/bin/python -V 2>&1)
  case "$VPY" in *3.12*) row PASS "venv" "$VPY" ;; *) row FAIL "venv" "$VPY — must be 3.12 (delete .venv and re-run ./install.sh)" ;; esac
else
  row FAIL "venv" ".venv/bin/python missing — re-run ./install.sh"
fi

# --- 4. index db (the real check) ----------------------------------------------
# Table name pinned from the indexer source: chunks_fts (FTS5) in index/kb.db.
DB="${KB_INSTALL_DIR:-$HOME/.claude/workato-kb}/index/kb.db"
if [ -f "$DB" ]; then
  FTS_N=$(sqlite3 "$DB" "select count(*) from chunks_fts" 2>/dev/null | tr -d ' ')
  [ "${FTS_N:-0}" != "0" ] && [ -n "$FTS_N" ] \
    && row PASS "index db" "$(du -h "$DB" | cut -f1), $FTS_N chunks" \
    || row FAIL "index db" "empty or unreadable ($DB) — re-run ./install.sh"
else
  row FAIL "index db" "missing at $DB — re-run ./install.sh"
fi

# --- 5. kb CLI + smoke search ---------------------------------------------------
if [ -x ".venv/bin/kb" ]; then
  row PASS "kb CLI" ".venv/bin/kb"
else
  row FAIL "kb CLI" ".venv/bin/kb missing — re-run ./install.sh"
fi
if [ -x ".venv/bin/kb" ]; then
  if SMOKE=$(WORKATO_KB_ROOT="${KB_INSTALL_DIR:-$HOME/.claude/workato-kb}" .venv/bin/kb search "pagination" 2>&1); then
    echo "$SMOKE" | grep -qE '"doc_id"|url|\[source' \
      && row PASS "smoke search" "results returned" \
      || row FAIL "smoke search" "no hits-shaped output — index broken? re-run ./install.sh"
  else
    row FAIL "smoke search" "kb search crashed: $(echo "$SMOKE" | head -1 | head -c 100)"
  fi
else
  row FAIL "smoke search" "no kb CLI to test"
fi

# --- 6. fractal coverage (the AIRTIGHT gate) -------------------------------------
# A coverage that is not AIRTIGHT is a WARN: the KB works, some surfaces just
# have gaps. Fix by re-running ./install.sh after editing knowledge/.
if [ -x ".venv/bin/kb" ]; then
  if COV=$(WORKATO_KB_ROOT="${KB_INSTALL_DIR:-$HOME/.claude/workato-kb}" .venv/bin/kb fractal coverage 2>&1); then
    if echo "$COV" | grep -qi "AIRTIGHT"; then
      row PASS "fractal coverage" "AIRTIGHT"
    else
      row WARN "fractal coverage" "not AIRTIGHT — $(echo "$COV" | grep -o '"verdict":[^,}]*' | head -1); re-run ./install.sh after adding knowledge files"
    fi
  else
    row WARN "fractal coverage" "audit crashed: $(echo "$COV" | head -1 | head -c 100)"
  fi
else
  row FAIL "fractal coverage" "no kb CLI to test"
fi

# --- 7. knowledge tree -----------------------------------------------------------
N_KB=$(find knowledge/domains -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
[ "${N_KB:-0}" -ge 1 ] && row PASS "knowledge" "$N_KB domain file(s)" \
  || row WARN "knowledge" "no knowledge/domains/*.md — curated playbooks land via promotion (Task 8)"

# --- 8. daemon (optional layer — CLI works cold without it) -----------------------
if lsof -iTCP:"${KB_PORT:-8765}" -sTCP:LISTEN >/dev/null 2>&1; then
  row PASS "daemon" "listening on port ${KB_PORT:-8765}"
else
  row WARN "daemon" "not running on port ${KB_PORT:-8765} (optional — the CLI loads the index cold)"
fi

echo
echo "== $PASS pass, $WARN warn, $FAIL fail =="
[ "$FAIL" -eq 0 ] && echo "Hard failures: none. WARNs are optional layers or degradation you can live with." \
  || echo "Fix FAILs (each names its fix), then re-run ./install.sh and ./verify.sh"
exit $(( FAIL > 0 ? 1 : 0 ))
