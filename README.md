# agent-recall

A cross-project index of **every Claude Code _and_ Codex conversation**, with
one-keystroke resume.

## The problem it solves

You run coding agents in many terminal tabs at once: multiple projects, multiple
conversations per project, sometimes Claude and sometimes Codex. When the
terminal app closes, the machine restarts, or you simply lose track of a tab,
it's hard to remember what you were doing and nearly impossible to find the
right conversation to resume.

## The key insight

You were never actually losing anything. **Both** agents write every
conversation to disk the moment it happens:

```
Claude   ~/.claude/projects/<encoded-dir>/<session-id>.jsonl
Codex    ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl
```

So there's nothing to save and nothing to maintain — this tool just reads what's
already there and gives you one ranked, fuzzy-searchable view across all projects
and both agents, then resumes the chosen one with the right command
(`claude --resume <id>` or `codex resume <id>`), `cd`-ing into its directory
first. Each agent's own `--resume`/`resume` only sees one agent and (for Claude)
only the current directory; `cs` unifies everything into one place.

Each row is tagged with its agent (`claude` / `codex`, color-coded). Filter to
one with `cs claude` or `cs codex`.

## Usage

```
cs                 fuzzy-pick any past conversation (all projects) → cd + resume
cs <query>         same, pre-filtered          e.g.  cs billing   cs api
cs list [query]    print a ranked table instead of the picker  (--all = no 40 cap)
cs name            list your custom-named conversations
cs name <sid> Foo  give a conversation your own name (sid may be an 8-char prefix)
cs name <sid>      show its current custom name
cs name <sid> -    clear the custom name
cs report          write dashboard.md + dashboard.html (grouped by project)
cs report --open   …and open the HTML dashboard in your browser
cs -o              shorthand for `cs report --open`
cs rebuild         force a full re-scan of the on-disk index
```

In the **picker**: type to filter, ↑/↓ to move, the preview pane shows the
session's dir / branch / msg count / last prompt / resume command, **Enter**
resumes (it `cd`s into the original directory first), **Ctrl-R** renames the
highlighted conversation inline, **Esc** cancels.

In the **HTML dashboard**: click any row to copy its `cd … && claude -r …`
command to the clipboard.

### Markers

- **agent tag** — every row shows `claude` (magenta) or `codex` (cyan).
- **● (green)** — the conversation is **open right now** in another tab.
  Claude-only (detected from `~/.claude/sessions/<pid>.json` + a pid liveness
  check); Codex doesn't expose an equivalent, so Codex rows never show ●.
- **★ (yellow)** — you **named** this conversation. Your name is shown instead of
  the auto-title/first-prompt, and it's searchable.

### Conversation names

The name shown for each conversation is chosen in this order:

1. **Your name** — Claude's `/rename` (written as a `custom-title` line in the
   transcript) or Codex's thread name (`session_index.jsonl`). The main way to
   name a convo: just `/rename` it inside the session.
2. **`cs name`** — a cs-only override set from outside a session (stored in
   `names.json`; via `cs name <sid> <text>` or Ctrl-R in the picker). Works for
   both agents.
3. **Auto label** — Claude's `aiTitle`; Codex has no auto-title, so it falls back
   to the **first prompt** of the session.
4. The last prompt, if nothing else exists.

### Codex specifics

- Resumed with `codex resume <id>`. The unified picker dispatches this
  automatically for Codex rows (Claude rows use `claude --resume`).
- Non-interactive `codex exec` runs (scripts, bots) are filtered out of the view.
- Codex desktop app chats (run from `~/Documents/Codex/<date>/<slug>/`) show under
  the `codex-app` project label.
- If the original working directory no longer exists, `cs` still asks the agent
  to resume the conversation but keeps you in the current directory and prints a
  warning.

## What each session shows

| Field | Source |
|---|---|
| directory | `cwd` in the transcript |
| created / last used | first / last message timestamp |
| name | `aiTitle` (Claude's auto-title), falling back to the last prompt |
| session id | the `.jsonl` filename (what `claude --resume` takes) |
| extras | message count, git branch, linked PR url |

Empty/abandoned sessions (no directory or zero messages) are hidden.

## How it's wired up

- `cs.py` — the scanner/engine (Python 3 stdlib only). Caches per-file parse
  results in `.cache.json` keyed by mtime+size, so warm reads are ~0.03s.
- `cs.sh` — the `cs` shell function. Portable: works sourced from **bash or
  zsh**, and self-locates so it runs from any install path. The picker has to
  change *your* shell's directory and launch `claude`, which a subprocess can't
  do, so this thin function consumes the picker's chosen `<cwd>\t<sid>` and runs
  the `cd` + agent resume command itself.
- Loaded via one line in your shell rc:
  `source "$HOME/path/to/agent-recall/cs.sh"`.

### Project labels

By default, `cs` shows compact project labels from the last meaningful path
components of each transcript's `cwd`.

If your work lives under one root and you want labels relative to it, set
`CS_PROJECT_ROOT` before sourcing `cs.sh`:

```
export CS_PROJECT_ROOT="$HOME/Code"
source "$HOME/path/to/agent-recall/cs.sh"
```

## Install on another machine

The engine reads `~/.claude/projects/**` of whatever user runs it, so it works
anywhere Claude Code runs.

- **Remote, one command:**
  `./deploy.sh user@host` copies `cs.py`, `cs.sh`, and `install.sh` to the target
  over SSH and runs the installer there.
- **On the machine itself:** copy the folder over and run `bash install.sh`.
  It installs to `~/.local/share/agent-recall`, wires the `source` line into
  `~/.bashrc` (and `~/.zshrc` if present), and checks `python3`/`fzf`.
  A numbered fallback works without `fzf`. Override the install dir with
  `CS_DEST=...`. On apt-based systems, set `CS_INSTALL_FZF=1` if you want the
  installer to attempt installing `fzf` for you.

After either, open a fresh shell on that machine and run `cs`.

## Requirements

- `fzf` (the picker; `brew install fzf`). Without it, `cs` falls back to a
  numbered menu.
- Python 3 (system Python is fine — no third-party packages).

## Development

Run the test suite with:

```
python3 -m unittest discover -s tests
```

The tests use temporary fake Claude/Codex transcript stores and do not read your
real session history.

## Generated local files

`cs` writes machine-local state next to `cs.py`:

- `.cache.json` — parsed transcript metadata cache
- `names.json` — names set with `cs name` or Ctrl-R in the picker
- `dashboard.html` / `dashboard.md` — generated reports

These files can contain local paths and conversation text, so they are ignored
by git and should not be committed.

## Design notes

- **Index-only.** It reads metadata Claude Code already captures. There's no
  hook and nothing runs in the background, so it works retroactively on every
  session that has ever been recorded.
- A future option (not built): a Stop hook that writes a richer "where we left
  off / next step" note at session end. The data model here would absorb it
  without changes.
