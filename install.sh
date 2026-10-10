#!/usr/bin/env bash
# workato-dev-kb installer
# Installs the local RAG++ knowledge pipeline: python 3.12 venv, bge models,
# corpus sync over the enabled public sources, FTS5+vector index, fractal
# projection, and proves the first `kb` query works before claiming success.
#
# Prerequisites (checked below): git, jq, python3.12 (brew is the fix engine).
#
# Failure behavior: every run logs to ./install-logs/install-<timestamp>.log.
# A non-zero exit prints which step failed, what completed, what did not, and
# the log path. A source that is down DEGRADES instead of aborting. The
# installer is idempotent — fix the cause and re-run; completed steps repeat
# or skip safely and there is no automatic rollback (none needed).
set -euo pipefail

cd "$(dirname "$0")"

# --- logging: capture every step's output to a timestamped log ---------------
# Everything the installer prints (stdout + stderr) is tee'd to the log, so a
# failed run can be triaged after the fact. The path is echoed up front and
# again on any failure.
LOG_DIR="$(pwd)/install-logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/install-$(date +%Y%m%d-%H%M%S).log"
# tee in process substitution: output still lands on the user's terminal
exec > >(tee -a "$LOG" 2>&1)
echo "install log: $LOG"

# --- step tracking, degraded-items list, temp cleanup, failure trap ----------
# STEPS is the ordered install pipeline. CURRENT_STEP is the one in flight.
# COMPLETED accumulates finished steps; DEGRADED accumulates non-fatal
# failures (installed-but-partial) that get a fix-it summary at the end.
STEPS=(prereqs venv config models sync index fractal smoke)
CURRENT_STEP="startup"
COMPLETED=()
DEGRADED=()
TMPFILES=()

begin_step() { # begin_step <name> — move to the next pipeline step
  CURRENT_STEP="$1"
  echo
  echo "== step: $1 =="
}

finish_step() { # finish_step <name> — mark it completed
  COMPLETED+=("$1")
}

degraded() { # degraded <description including the exact fix command>
  echo "  DEGRADED: $1"
  DEGRADED+=("$1")
}

fail_step() { # fail_step <message> — fatal; the EXIT trap adds context
  echo "FAIL: $1"
  exit 1
}

cleanup_tmp() { # remove any temp files this run created (never returns nonzero:
                # a 1 here would trip set -e inside the EXIT trap)
  local t
  [ "${#TMPFILES[@]}" -gt 0 ] || return 0
  for t in "${TMPFILES[@]}"; do
    if [ -n "$t" ] && [ -e "$t" ]; then rm -f "$t"; fi
  done
  return 0
}

on_exit() { # single place that reports a failed run
  local rc=$?
  cleanup_tmp
  [ "$rc" -eq 0 ] && return
  local not_done="" seen=0 s
  for s in "${STEPS[@]}"; do
    [ "$s" = "$CURRENT_STEP" ] && seen=1
    [ "$seen" = 1 ] && not_done="$not_done $s"
  done
  echo
  echo "INSTALL FAILED (exit $rc) at step: $CURRENT_STEP"
  if [ "${#COMPLETED[@]}" -gt 0 ]; then
    echo "  completed before failure:${COMPLETED[*]/#/ }"
  else
    echo "  completed before failure: none"
  fi
  echo "  not completed:$not_done"
  echo "  work already done is idempotent — no rollback was attempted and none"
  echo "  is needed; re-running ./install.sh repeats or skips steps safely."
  if [ "${#DEGRADED[@]}" -gt 0 ]; then
    echo "  degraded items from this run (fix these too):"
    local d
    for d in "${DEGRADED[@]}"; do echo "    - $d"; done
  fi
  echo "  Full output: $LOG"
  echo "  Resume: fix the cause above, then re-run ./install.sh (safe to re-run)."
}
trap on_exit EXIT
trap 'echo "install.sh: command failed at line $LINENO (step: $CURRENT_STEP)" >&2' ERR

# --- retries + error classification for network-shaped steps -----------------
classify_failure() { # classify_failure <desc> <rc> <output-file>
  local desc="$1" rc="$2" out="$3" hint
  if grep -qiE 'could not resolve host|connection (timed out|refused)|failed to connect|network|SSL|curl: \([0-9]+\)' "$out" 2>/dev/null; then
    hint="network — check your connection/proxy/VPN and try again"
  elif grep -qiE 'permission denied|EACCES' "$out" 2>/dev/null; then
    hint="permissions — check ownership of the target directory"
  elif grep -qiE 'authentication|401|403|invalid credentials' "$out" 2>/dev/null; then
    hint="auth — a token or credential is missing/rejected"
  elif grep -qiE 'command not found|not a git repository' "$out" 2>/dev/null; then
    hint="tool/version — a required command is missing or too old"
  else
    hint="unclassified — the full output above is in the log"
  fi
  echo "FAIL: $desc (exit $rc) — $hint"
}

with_retry() { # with_retry <desc> <attempts> <cmd...> — bounded retries with
               # short sleeps; classifies the failure after exhaustion
  local desc="$1" attempts="$2"; shift 2
  local i rc=0
  local out; out="$(mktemp -t kbretry)"; TMPFILES+=("$out")
  for i in $(seq 1 "$attempts"); do
    echo "  attempt $i/$attempts: $desc"
    if "$@" >"$out" 2>&1; then
      cat "$out"
      rm -f "$out"
      return 0
    else
      # capture INSIDE the else: after `fi`, $? is the if-statement's status (0)
      rc=$?
    fi
    cat "$out"
    if [ "$i" -lt "$attempts" ]; then
      echo "  retrying in 3s..."
      sleep 3
    fi
  done
  classify_failure "$desc" "$rc" "$out"
  rm -f "$out"
  return "$rc"
}

run_fixed() { eval "$1"; }  # lets with_retry wrap a command string

with_retry_stream() { # with_retry_stream <desc> <attempts> <cmd...> — same
                      # bounded retries, but output streams live (needed for
                      # interactive fixers like the Homebrew installer that
                      # prompt for a password; no capture, so no classify)
  local desc="$1" attempts="$2"; shift 2
  local i rc=0
  for i in $(seq 1 "$attempts"); do
    echo "  attempt $i/$attempts: $desc"
    if "$@"; then
      return 0
    else
      rc=$?
    fi
    if [ "$i" -lt "$attempts" ]; then
      echo "  retrying in 3s..."
      sleep 3
    fi
  done
  echo "FAIL: $desc (exit $rc) — see the output above; full log: $LOG"
  return "$rc"
}

echo "== workato-dev-kb installer =="

# --- interactive detection + fix engine ---------------------------------------
INTERACTIVE="[ -t 0 ]"

fix_or_fail() {  # $1 what, $2 fix command
  if $INTERACTIVE; then
    printf "  %s is missing/outdated. Fix it now with: %s\n      [Y/n] " "$1" "$2"
    read -r reply
    if [[ ! "$reply" =~ ^[Nn] ]]; then
      echo "  running: $2"
      if ! with_retry_stream "fix: $1" 3 run_fixed "$2"; then
        fail_step "the fix command for '$1' failed after retries — run it yourself ('${2}') and re-run ./install.sh"
      fi
    else
      fail_step "$1 is required. Install it ('${2}'), then re-run ./install.sh"
    fi
  else
    echo "FAIL: $1 missing/outdated (headless mode — run this yourself, then re-run ./install.sh):"
    echo "  ${2}"
    exit 1
  fi
}

ensure() {  # $1 binary, $2 fix command
  command -v "$1" >/dev/null || fix_or_fail "$1" "$2"
  command -v "$1" >/dev/null || { echo "FAIL: $1 still not on PATH after the fix (PATH may need: export PATH=...); re-run ./install.sh"; exit 1; }
  echo "  ok: $1 ($($1 --version 2>/dev/null | head -1 | head -c 60))"
}

# --- step: prereqs — check, offer to fix, re-verify ----------------------------
# brew is the fix engine for everything below it. Disk-space preflight runs
# before any download: models + index need ~5GB.
begin_step prereqs

if ! command -v brew >/dev/null; then
  fix_or_fail "Homebrew" '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
fi
ensure git "brew install git"
ensure jq "brew install jq"
if ! command -v python3.12 >/dev/null; then
  fix_or_fail "python3.12 (the KB venv needs it; torch has no 3.14 wheels)" "brew install python@3.12"
fi
ensure python3.12 "brew install python@3.12"

FREE_GB=$(df -g "$HOME" | awk 'NR==2 {print $4}')
[ "${FREE_GB:-0}" -ge 5 ] || fail_step "only ${FREE_GB:-0}GB free — models + index need ~5GB. Free space and re-run."
echo "  ok: ${FREE_GB}GB free"

finish_step prereqs

# --- step: venv (with repair) --------------------------------------------------
# A stale or wrong-python venv is the top KB-onboarding failure: detect and
# rebuild it rather than failing obscurely deep inside sentence-transformers.
begin_step venv
VENV=".venv"
if [ -f "$VENV/pyvenv.cfg" ] && ! grep -q "python3.12" "$VENV/pyvenv.cfg"; then
  degraded "existing .venv is not python3.12 — removing and recreating (old venv deleted; re-run ./install.sh to finish)"
  rm -rf "$VENV"
fi
if [ ! -f "$VENV/bin/activate" ]; then
  with_retry "create python3.12 venv" 3 python3.12 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install -q --upgrade pip
with_retry "install kb dependencies (torch, sentence-transformers, sqlite-vec)" 3 \
  pip install -q sentence-transformers sqlite-vec
finish_step venv

# --- step: config ----------------------------------------------------------------
# Two settings, not fourteen: where the corpus/index lives (KB_INSTALL_DIR) and
# the optional daemon port. No config.sh + interactive -> prompt inline;
# headless -> copy the example with a NOTE. The install dir must sit OUTSIDE
# the repo checkout: the corpus is a multi-GB gitignored tree, and keeping it
# out of the clone is how accidental giant commits never happen.
begin_step config
if [ ! -f config.sh ]; then
  if [ -t 0 ]; then
    echo "No config.sh found — 2 questions (press Enter to accept each default):"
    printf "  KB_INSTALL_DIR (corpus + index root) [%s/.claude/workato-kb]: " "$HOME"
    read -r reply
    CFG_DIR="${reply:-$HOME/.claude/workato-kb}"
    printf "  KB_PORT (daemon port, optional) [8765]: "
    read -r reply
    CFG_PORT="${reply:-8765}"
    {
      echo "# workato-dev-kb consumer config (generated by install.sh)"
      echo "KB_INSTALL_DIR=\"$CFG_DIR\""
      echo "KB_PORT=\"$CFG_PORT\""
    } > config.sh
    echo "  wrote config.sh"
  else
    cp config.example.sh config.sh
    echo "NOTE: no config.sh and stdin is not a terminal, so onboarding was skipped."
    echo "      Installed from the example — edit config.sh by hand, then re-run ./install.sh"
  fi
fi
# shellcheck disable=SC1091
source config.sh
# a hand-written config.sh may have dropped the defaults
: "${KB_INSTALL_DIR:=$HOME/.claude/workato-kb}"
: "${KB_PORT:=8765}"
REPO_DIR="$(pwd)"
case "${KB_INSTALL_DIR%/}" in
  "$REPO_DIR"|"$REPO_DIR"/*)
    fail_step "KB_INSTALL_DIR (${KB_INSTALL_DIR}) is inside the repo checkout — corpus + index data must live outside the clone (it is a multi-GB tree). Edit config.sh and re-run ./install.sh"
    ;;
esac
# the pipeline's own data-root knob: make the KB code honor KB_INSTALL_DIR
export WORKATO_KB_ROOT="$KB_INSTALL_DIR"
echo "  ok: corpus + index at $KB_INSTALL_DIR"
finish_step config

# --- KB steps (models, sync, index, fractal, smoke) land in Task 5 -------------
# Until those steps are appended, stop honestly here rather than exiting 0 with
# an install that did not run the pipeline.
CURRENT_STEP="models"
fail_step "KB steps (models sync index fractal smoke) are not in this build yet — installer incomplete"
