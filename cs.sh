# agent-recall — cross-project agent conversation index + resume.
# Portable: works sourced from bash OR zsh, locates itself wherever installed.
# Source from your shell rc:   source /path/to/agent-recall/cs.sh
#
#   cs               fuzzy-pick any past conversation (all projects) -> cd + resume
#   cs <query>       same, pre-filtered (e.g. `cs api`)
#   cs list [q]      print a ranked table instead of the picker (--all for everything)
#   cs name …        set/show/clear a cs-only name for a conversation
#   cs report [-o]   write dashboard.md + dashboard.html (-o / --open to open it)
#   cs rebuild       force a full re-scan of the on-disk index
# In the picker: Enter resumes · Ctrl-R renames · Esc cancels.
# The name shown per convo = your Claude /rename name, then a cs name, then the
# auto-title.  Markers:  ● open right now in another tab   ★ you named it.

# --- locate this file regardless of shell / install path ---
if [ -n "${ZSH_VERSION:-}" ]; then
  _CS_SELF="${(%):-%x}"
elif [ -n "${BASH_VERSION:-}" ]; then
  _CS_SELF="${BASH_SOURCE[0]}"
else
  _CS_SELF="$0"
fi
_CS_DIR="$(cd "$(dirname "$_CS_SELF")" >/dev/null 2>&1 && pwd)"
_CS_PY="$_CS_DIR/cs.py"
unset _CS_SELF

cs() {
  case "$1" in
    list|ls|name|report|rebuild|preview|_picklines|_rename|help|-h|--help)
      command python3 "$_CS_PY" "$@"
      ;;
    -o|--open)  # convenience: `cs -o` == `cs report --open`
      command python3 "$_CS_PY" report --open
      ;;
    *)
      local sel dir sid agent rest
      sel="$(command python3 "$_CS_PY" pick "$@")" || return
      [ -z "$sel" ] && return
      dir="${sel%%$'\t'*}"          # field 1: cwd
      rest="${sel#*$'\t'}"          # "sid<TAB>agent"
      sid="${rest%%$'\t'*}"         # field 2: session id
      agent="${rest##*$'\t'}"       # field 3: claude | codex
      if [ -d "$dir" ]; then
        builtin cd "$dir" || return
      else
        printf '%s\n' "⚠ original directory is gone: $dir (resuming from here)" >&2
      fi
      case "$agent" in
        codex) command codex resume "$sid" ;;
        *)     command claude --resume "$sid" ;;
      esac
      ;;
  esac
}
