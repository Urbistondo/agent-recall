#!/usr/bin/env bash
# agent-recall installer — runs ON the target machine (bash).
# Copies cs.py + cs.sh into place, wires them into the shell rc, checks deps.
# Idempotent: safe to re-run. Honors $CS_DEST to override the install dir.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" >/dev/null 2>&1 && pwd)"
DEST="${CS_DEST:-$HOME/.local/share/agent-recall}"

echo "agent-recall installer"
echo "  source : $SRC_DIR"
echo "  install: $DEST"

mkdir -p "$DEST"
cp "$SRC_DIR/cs.py" "$SRC_DIR/cs.sh" "$DEST/"
chmod +x "$DEST/cs.py" || true

# --- wire into shell rc(s) ---
line="source \"$DEST/cs.sh\""
wired=0
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
  [ -e "$rc" ] || continue
  if grep -qF "$DEST/cs.sh" "$rc" 2>/dev/null; then
    echo "  already wired in $rc"
  else
    printf '\n# agent-recall (cs) — cross-project agent conversation index\n%s\n' "$line" >> "$rc"
    echo "  wired into $rc"
  fi
  wired=1
done
if [ "$wired" = 0 ]; then           # no rc existed at all -> create .bashrc
  printf '\n# agent-recall (cs)\n%s\n' "$line" >> "$HOME/.bashrc"
  echo "  created ~/.bashrc and wired in"
fi

# --- dependencies ---
if ! command -v python3 >/dev/null 2>&1; then
  echo "  WARNING: python3 not found — cs needs it. Install python3 and re-run."
fi
if command -v fzf >/dev/null 2>&1; then
  echo "  fzf present ($(fzf --version 2>/dev/null | head -1))"
else
  echo "  fzf missing — the numbered fallback works, but install fzf for the fuzzy picker"
  if [ "${CS_INSTALL_FZF:-0}" = "1" ] && command -v apt-get >/dev/null 2>&1; then
    echo "  CS_INSTALL_FZF=1 set — installing fzf with apt-get"
    (apt-get update -qq && apt-get install -y -qq fzf && echo "  fzf installed") \
      || echo "  could not install fzf automatically — numbered fallback will be used"
  fi
fi

# --- build index + show a sample so we know it found the transcripts ---
echo "---"
python3 "$DEST/cs.py" rebuild >/dev/null 2>&1 || true
n=$(find "$HOME/.claude/projects" -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' ') || n=0
echo "  found ${n:-0} transcript file(s) under ~/.claude/projects"
python3 "$DEST/cs.py" list 2>/dev/null | head -8 || true
echo "---"
echo "Installed. Open a NEW shell (or 'source ~/.bashrc') then run:  cs"
