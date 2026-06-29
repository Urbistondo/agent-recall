#!/usr/bin/env python3
"""
cs — one cross-project index of every Claude Code AND Codex conversation.

Both agents already write every conversation to disk:
  Claude  ~/.claude/projects/<encoded-dir>/<session-id>.jsonl
  Codex   ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl
This tool reads both, gives you ONE ranked view across ALL projects and both
agents, and resumes any conversation with the right command (claude --resume
or codex resume). Nothing to save, nothing to maintain; it works retroactively
on every session you've ever had.

Subcommands:
  pick [query]      Interactive fzf picker -> prints "<cwd>\\t<sid>" of the choice
                    (the `cs` shell function consumes this to cd + claude -r).
                    In the picker: Enter resumes · Ctrl-R renames · Esc cancels.
  list [query]      Print a ranked table (newest first). --all for everything.
  name [sid [text]] Set/show/clear your own name for a session.
                      cs name              list all custom-named sessions
                      cs name <sid>        show current name
                      cs name <sid> Foo…   set the name (sid may be an 8-char prefix)
                      cs name <sid> -      clear the name
  report [--open]   Write dashboard.md + dashboard.html next to this script.
  preview <sid>     Print details for one session (used by fzf's preview pane).
  rebuild           Force a full re-scan (ignore cache).

Markers:  ●  conversation is OPEN right now in another tab   ★  has a custom name
"""

import json
import os
import sys
import glob
import html
import datetime
import shlex
import subprocess
from pathlib import Path

PROJECTS = Path(os.path.expanduser("~/.claude/projects"))
SESSIONS_DIR = Path(os.path.expanduser("~/.claude/sessions"))
CODEX_SESSIONS = Path(os.path.expanduser("~/.codex/sessions"))
CODEX_INDEX = Path(os.path.expanduser("~/.codex/session_index.jsonl"))
HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache.json"
NAMES = HERE / "names.json"
HOME = os.path.expanduser("~")

# ----------------------------------------------------------------------------- parsing

def parse_claude(path):
    """Extract the resume-relevant metadata from one Claude .jsonl transcript."""
    sid = os.path.basename(path)[:-6]  # strip .jsonl
    cwd = title = last_prompt = branch = pr_url = custom_title = None
    first_ts = last_ts = None
    n_user = 0
    try:
        with open(path, "r", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("cwd") and not cwd:
                    cwd = o["cwd"]
                if o.get("aiTitle"):
                    title = o["aiTitle"]
                if o.get("customTitle"):          # set by Claude's /rename command
                    custom_title = o["customTitle"]
                if o.get("lastPrompt"):
                    last_prompt = o["lastPrompt"]
                if o.get("gitBranch"):
                    branch = o["gitBranch"]
                if o.get("prUrl"):
                    pr_url = o["prUrl"]
                ts = o.get("timestamp")
                if ts:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts
                if o.get("type") == "user" and not o.get("isSidechain"):
                    n_user += 1
    except Exception:
        return None
    if not first_ts:
        return None
    return {
        "sid": sid,
        "cwd": cwd or "",
        "title": title or "",
        "custom_title": custom_title or "",
        "last_prompt": last_prompt or "",
        "branch": branch or "",
        "pr_url": pr_url or "",
        "created": first_ts,
        "used": last_ts,
        "msgs": n_user,
        "agent": "claude",
        "origin": "",
    }


def parse_codex(path):
    """Extract metadata from one Codex rollout transcript
    (~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl). The opening
    `session_meta` record carries session_id + cwd; the real typed prompts are
    `event_msg` records of type `user_message`."""
    sid = cwd = origin = None
    created = last_ts = first_prompt = last_prompt = None
    n_user = 0
    try:
        with open(path, "r", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
                ts = o.get("timestamp")
                if ts:
                    if created is None:
                        created = ts
                    last_ts = ts
                t = o.get("type")
                if t == "session_meta":
                    sid = p.get("session_id") or sid
                    cwd = p.get("cwd") or cwd
                    origin = p.get("source") or p.get("originator") or origin
                    if p.get("timestamp"):
                        created = p["timestamp"]
                elif t == "event_msg" and p.get("type") == "user_message":
                    msg = (p.get("message") or "").strip()
                    if msg:
                        n_user += 1
                        if first_prompt is None:
                            first_prompt = msg
                        last_prompt = msg
    except Exception:
        return None
    if not sid:  # fall back to the uuid embedded in the filename
        sid = os.path.basename(path)[:-6].split("rollout-")[-1][20:]
    if not created:
        return None
    return {
        "sid": sid,
        "cwd": cwd or "",
        "title": first_prompt or "",
        "custom_title": "",          # filled from session_index later
        "last_prompt": last_prompt or "",
        "branch": "",
        "pr_url": "",
        "created": created,
        "used": last_ts or created,
        "msgs": n_user,
        "agent": "codex",
        "origin": origin or "",
    }


def codex_thread_names():
    """sessionId -> thread_name from Codex's session_index.jsonl (its /rename)."""
    d = {}
    try:
        with open(CODEX_INDEX, errors="replace") as fh:
            for line in fh:
                o = json.loads(line)
                if o.get("id") and o.get("thread_name"):
                    d[o["id"]] = o["thread_name"]
    except Exception:
        pass
    return d


def load_names():
    try:
        return json.loads(NAMES.read_text())
    except Exception:
        return {}


def save_names(d):
    NAMES.write_text(json.dumps(d, indent=2, ensure_ascii=False))


def live_sessions():
    """sessionId -> status for Claude sessions whose OS process is still alive.
    Claude writes a per-process file to ~/.claude/sessions/<pid>.json while a
    session runs; we treat a session as 'open now' only if that pid is live
    (the files can linger after a crash)."""
    live = {}
    for p in glob.glob(str(SESSIONS_DIR / "*.json")):
        try:
            o = json.loads(Path(p).read_text())
            pid, sid = o.get("pid"), o.get("sessionId")
            if not pid or not sid:
                continue
            os.kill(pid, 0)  # raises OSError if the process is gone
            live[sid] = o.get("status", "open")
        except Exception:
            continue
    return live


def build_index(force=False):
    """Scan all transcripts, using a per-file mtime/size cache for speed.
    Custom names and live-status are layered on fresh each call (not cached)."""
    cache = {}
    if CACHE.exists() and not force:
        try:
            cache = json.loads(CACHE.read_text())
        except Exception:
            cache = {}
    rows, new_cache = [], {}
    stores = [
        (glob.glob(str(PROJECTS / "*" / "*.jsonl")), parse_claude),
        (glob.glob(str(CODEX_SESSIONS / "**" / "*.jsonl"), recursive=True), parse_codex),
    ]
    for paths, parser in stores:
        for path in paths:
            try:
                st = os.stat(path)
            except OSError:
                continue
            sig = f"v3:{st.st_mtime_ns}:{st.st_size}"  # bump prefix to invalidate old cache
            cached = cache.get(path)
            if cached and cached.get("sig") == sig and cached.get("data"):
                data = cached["data"]
            else:
                data = parser(path)
                if data is None:
                    continue
            new_cache[path] = {"sig": sig, "data": data}
            data = dict(data)
            data["mtime"] = st.st_mtime
            rows.append(data)
    try:
        CACHE.write_text(json.dumps(new_cache))
    except Exception:
        pass
    names, live, tnames = load_names(), live_sessions(), codex_thread_names()
    for r in rows:
        r["name"] = names.get(r["sid"], "")
        if r.get("agent") == "codex" and not r.get("custom_title"):
            r["custom_title"] = tnames.get(r["sid"], "")
        r["live"] = r["sid"] in live
        r["status"] = live.get(r["sid"], "")
    rows.sort(key=lambda r: r.get("used") or "", reverse=True)
    return rows

# ----------------------------------------------------------------------------- helpers

PROJECT_ROOT = os.environ.get("CS_PROJECT_ROOT", "")
_CONTAINER_DIRS = {"Code", "Projects", "Developer", "Workspace", "work", "src"}
_NOISE_DIRS = {"apps", "clients", "packages", "services"}


def proj_label(cwd):
    """Human-readable project id for tables and reports.

    If CS_PROJECT_ROOT is set, labels are relative to that root. Otherwise, use
    the last meaningful one or two path components so the display stays compact
    without assuming a particular local directory layout.
    """
    if not cwd:
        return "?"
    if "/Documents/Codex/" in cwd:   # Codex desktop-app per-session scratch dirs
        return "codex-app"
    root = PROJECT_ROOT.rstrip("/")
    if root and (cwd == root or cwd.startswith(root + "/")):
        if cwd == root:
            return Path(root).name or "~"
        parts = cwd[len(root) + 1:].split("/")
        if ".worktrees" in parts:  # show as <project>:<worktree>
            i = parts.index(".worktrees")
            base = [x for x in parts[:i] if x not in _NOISE_DIRS]
            wt = parts[i + 1] if i + 1 < len(parts) else "wt"
            return f"{(base[-1] if base else Path(root).name or '~')}:{wt}"
        parts = [x for x in parts if x not in _NOISE_DIRS]
        return "/".join(parts) if parts else (Path(root).name or "~")

    parts = [x for x in cwd.replace(HOME, "~").split("/") if x and x != "~"]
    if ".worktrees" in parts:
        i = parts.index(".worktrees")
        base = [x for x in parts[:i] if x not in _CONTAINER_DIRS and x not in _NOISE_DIRS]
        wt = parts[i + 1] if i + 1 < len(parts) else "wt"
        return f"{(base[-1] if base else 'worktree')}:{wt}"
    parts = [x for x in parts if x not in _CONTAINER_DIRS and x not in _NOISE_DIRS]
    return "/".join(parts[-2:]) if parts else cwd


def ago(ts):
    try:
        d = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        delta = datetime.datetime.now(datetime.timezone.utc) - d
        s = delta.total_seconds()
        if s < 3600:
            return f"{int(s/60)}m ago"
        if s < 86400:
            return f"{int(s/3600)}h ago"
        if s < 86400 * 14:
            return f"{int(s/86400)}d ago"
        return d.strftime("%b %-d")
    except Exception:
        return "?"


def clip(s, n):
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def label_for(r):
    """Name priority: your /rename name (custom-title in the transcript), then a
    cs-only name, then Claude's auto-title, then the last prompt."""
    return (r.get("custom_title") or r.get("name") or r["title"]
            or r["last_prompt"] or "(untitled)")


def is_named(r):
    """True if you gave this conversation a name (via /rename or `cs name`)."""
    return bool(r.get("custom_title") or r.get("name"))


def active(rows):
    """Conversations worth resuming: has a directory, at least one real message,
    and isn't a Codex non-interactive `exec` run (scripts/bots)."""
    out = []
    for r in rows:
        if not r["cwd"] or r["msgs"] <= 0:
            continue
        if r.get("origin") == "exec":
            continue
        out.append(r)
    return out


_GRN, _YEL, _DIM, _RST = "\033[32m", "\033[33m", "\033[2m", "\033[0m"
_AGENT_COL = {"claude": "\033[35m", "codex": "\033[36m"}  # magenta / cyan


def row_display(r, name_width=50):
    """One scannable line per conversation, NAME FIRST and prominent, with the
    agent tag + age/project/msgs demoted to dimmed metadata on the right."""
    dot = f"{_GRN}●{_RST}" if r.get("live") else " "
    star = f"{_YEL}★{_RST}" if is_named(r) else " "
    name = clip(label_for(r), name_width)
    pad = " " * max(0, name_width - len(name))
    agent = r.get("agent", "claude")
    tag = f"{_AGENT_COL.get(agent, '')}{agent:<6}{_RST}"
    meta = f"{tag} {_DIM}{clip(proj_label(r['cwd']),20):<20} {r['msgs']:>4} msg {ago(r['used']):>7}{_RST}"
    return f"{dot} {star} {name}{pad}  {meta}"


def resolve_sid(prefix):
    """Turn an 8-char (or longer) prefix into a full session id."""
    matches = [r for r in build_index() if r["sid"].startswith(prefix)]
    if len(matches) == 1:
        return matches[0]["sid"]
    if not matches:
        sys.stderr.write(f"No session matches '{prefix}'.\n")
    else:
        sys.stderr.write(f"Ambiguous '{prefix}' — {len(matches)} matches, use more characters.\n")
    return None

# ----------------------------------------------------------------------------- display

def pick_lines():
    """Build the fzf input lines. Display chunk first, then hidden TAB fields:
    <display>\\t<sid>\\t<cwd>  (fzf shows + searches only the display chunk)."""
    out = []
    for r in active(build_index()):
        out.append(f"{row_display(r)}\t{r['sid']}\t{r['cwd']}\t{r.get('agent','claude')}")
    return out

# ----------------------------------------------------------------------------- commands

def cmd_list(args):
    show_all = "--all" in args
    query = " ".join(a for a in args if not a.startswith("-")).lower()
    rows = active(build_index())
    if query:
        rows = [r for r in rows if query in
                (r["cwd"] + " " + label_for(r) + " " + r.get("name", "") + " " + r.get("agent", "")).lower()]
    if not show_all:
        rows = rows[:40]
    if not rows:
        print("No matching sessions.")
        return
    n_live = sum(1 for r in rows if r.get("live"))
    extra = f"  ·  ● {n_live} open now" if n_live else ""
    print(f"\n  {len(rows)} session(s)  ·  newest first{extra}\n")
    for r in rows:
        print("  " + row_display(r))
        tail = f"  ·  {r['status']}" if r.get("live") else ""
        print(f"        \033[2m{r['sid']}{tail}\033[0m")
    print()


def cmd_pick(args):
    query = " ".join(a for a in args if not a.startswith("-"))
    rows = active(build_index())
    if not rows:
        sys.stderr.write("No resumable sessions found.\n")
        return 1
    me = shlex.quote(str(Path(__file__).resolve()))
    fzf = [
        "fzf",
        "--delimiter", "\t",
        "--with-nth", "1",
        "--nth", "1",
        "--ansi",
        "--height", "80%",
        "--reverse",
        "--prompt", "resume › ",
        "--header", "↑↓ choose · Enter resume (claude/codex auto) · Ctrl-R rename · Esc cancel   (● open  ★ named)",
        "--preview", f"python3 {me} preview {{2}}",
        "--preview-window", "down,42%,wrap",
        "--bind", f"ctrl-r:execute(python3 {me} _rename {{2}} < /dev/tty > /dev/tty)+reload(python3 {me} _picklines)",
    ]
    if query:
        fzf += ["--query", query]
    try:
        proc = subprocess.run(fzf, input="\n".join(pick_lines()), text=True,
                              stdout=subprocess.PIPE)
    except FileNotFoundError:
        return _pick_fallback(rows)
    if proc.returncode != 0 or not proc.stdout.strip():
        return 1
    chosen = proc.stdout.strip().split("\t")
    if len(chosen) < 4:
        return 1
    sid, cwd, agent = chosen[1], chosen[2], chosen[3]
    print(f"{cwd}\t{sid}\t{agent}")  # shell function reads "<cwd>\t<sid>\t<agent>"
    return 0


def _pick_fallback(rows):
    rows = rows[:40]
    for i, r in enumerate(rows, 1):
        mark = "●" if r.get("live") else " "
        sys.stderr.write(f"{i:>2}. {mark} {ago(r['used']):>8}  {proj_label(r['cwd']):<22}  {clip(label_for(r),50)}\n")
    sys.stderr.write("Pick #: ")
    sys.stderr.flush()
    try:
        r = rows[int(sys.stdin.readline().strip()) - 1]
    except Exception:
        return 1
    print(f"{r['cwd']}\t{r['sid']}\t{r.get('agent','claude')}")
    return 0


def cmd_preview(args):
    if not args:
        return
    sid = args[0]
    for r in build_index():
        if r["sid"] == sid:
            agent = r.get("agent", "claude")
            col = _AGENT_COL.get(agent, "")
            print(f"\033[1m{label_for(r)}\033[0m\n")
            print(f"  agent   {col}{agent}{_RST}" + (f" ({r['origin']})" if r.get("origin") else ""))
            if r.get("live"):
                print(f"  \033[32m● OPEN NOW ({r['status']})\033[0m — already running in another tab")
            if r.get("custom_title"):
                print(f"  \033[33m★ name\033[0m (first prompt: {clip(r['title'],60) or '—'})")
            elif r.get("name"):
                print(f"  \033[33m★ cs name\033[0m (auto-title: {clip(r['title'],60) or '—'})")
            print(f"  dir     {r['cwd'].replace(HOME,'~')}")
            if agent == "claude":
                print(f"  branch  {r['branch'] or '—'}")
            print(f"  msgs    {r['msgs']}")
            print(f"  created {ago(r['created'])}")
            print(f"  used    {ago(r['used'])}")
            if r["pr_url"]:
                print(f"  PR      {r['pr_url']}")
            if r["last_prompt"]:
                print(f"\n  last prompt:\n  \033[2m{clip(r['last_prompt'],300)}\033[0m")
            resume = (f"codex resume {r['sid'][:8]}…" if agent == "codex"
                      else f"claude -r {r['sid'][:8]}…")
            print(f"\n  resume:  cd {r['cwd'].replace(HOME,'~')} && {resume}")
            return


def cmd_name(args):
    names = load_names()
    if not args:  # list named sessions
        if not names:
            print("No custom names yet.\n  Set one:  cs name <session-id> Your name here")
            return
        idx = {r["sid"]: r for r in build_index()}
        for sid, nm in names.items():
            r = idx.get(sid)
            where = f"  ({proj_label(r['cwd'])})" if r else "  (transcript gone)"
            print(f"  ★ {sid[:8]}  {nm}{where}")
        return
    sid = resolve_sid(args[0])
    if not sid:
        return
    if len(args) == 1:  # show current
        cur = names.get(sid)
        print(f"  ★ {sid[:8]}  {cur}" if cur else f"  {sid[:8]} has no custom name")
        return
    rest = args[1:]
    if rest in (["-"], ["--clear"]):
        names.pop(sid, None)
        save_names(names)
        print(f"  cleared custom name for {sid[:8]}")
        return
    nm = " ".join(rest)
    names[sid] = nm
    save_names(names)
    print(f"  ★ {sid[:8]} → {nm}")


def cmd_rename_interactive(args):
    """Called by the picker's Ctrl-R binding; args[0] is a full session id."""
    if not args:
        return
    sid = args[0]
    cur = load_names().get(sid, "")
    sys.stderr.write(f"New name for {sid[:8]}" + (f" (was: {cur})" if cur else "") + ", empty to cancel: ")
    sys.stderr.flush()
    try:
        nm = sys.stdin.readline().strip()
    except Exception:
        return
    names = load_names()
    if nm:
        names[sid] = nm
    else:
        return
    save_names(names)


def cmd_report(args):
    rows = active(build_index())
    groups = {}
    for r in rows:
        groups.setdefault(r["cwd"], []).append(r)
    ordered = sorted(groups.items(),
                     key=lambda kv: max(x["used"] or "" for x in kv[1]), reverse=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    def resume_cmd(r):
        return (f"codex resume {r['sid']}" if r.get("agent") == "codex"
                else f"claude -r {r['sid']}")

    # ---- markdown
    md = [f"# Agent Sessions · {len(rows)} active · {now}\n"]
    for cwd, items in ordered:
        md.append(f"\n## {cwd.replace(HOME,'~')}  ({len(items)})\n")
        for r in sorted(items, key=lambda x: x["used"] or "", reverse=True):
            mark = "● " if r.get("live") else ""
            md.append(f"- {mark}**{ago(r['used'])}** · `{r.get('agent','claude')}` · {clip(label_for(r),80)}  ")
            md.append(f"  `cd {cwd.replace(HOME,'~')} && {resume_cmd(r)}`")
    (HERE / "dashboard.md").write_text("\n".join(md))

    # ---- html (click a row to copy its resume command)
    cards = []
    for cwd, items in ordered:
        rowsh = []
        for r in sorted(items, key=lambda x: x["used"] or "", reverse=True):
            cmd = f"cd {cwd} && {resume_cmd(r)}"
            live = " live" if r.get("live") else ""
            dot = "<span class=dot>●</span> " if r.get("live") else ""
            star = "★ " if is_named(r) else ""
            agent = r.get("agent", "claude")
            rowsh.append(
                f"<tr class='row{live}' onclick=\"cp(this)\" data-cmd=\"{html.escape(cmd, quote=True)}\">"
                f"<td class=age>{dot}{ago(r['used'])}</td>"
                f"<td class='ag {agent}'>{agent}</td>"
                f"<td class=ttl>{html.escape(star + clip(label_for(r),90))}</td>"
                f"<td class=meta>{r['msgs']} msgs</td></tr>"
            )
        cards.append(
            f"<section><h2>{html.escape(cwd.replace(HOME,'~'))} "
            f"<span class=count>{len(items)}</span></h2>"
            f"<table>{''.join(rowsh)}</table></section>"
        )
    page = f"""<!doctype html><meta charset=utf-8>
<title>Agent Recall</title>
<style>
  :root{{color-scheme:dark}}
  body{{background:#0a0a0f;color:#e7e7ea;font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;margin:0;padding:32px;max-width:1100px}}
  h1{{font-size:20px;font-weight:600;margin:0 0 4px}}
  .sub{{color:#7c7c8a;margin-bottom:28px}}
  section{{margin-bottom:26px}}
  h2{{font-size:13px;color:#9b9bf5;font-weight:600;margin:0 0 6px;display:flex;gap:8px;align-items:center}}
  .count{{color:#55556a;font-weight:400}}
  table{{width:100%;border-collapse:collapse}}
  tr{{cursor:pointer;border-bottom:1px solid #18181f}}
  tr:hover{{background:#14141c}}
  td{{padding:7px 10px;vertical-align:top}}
  .age{{color:#7c7c8a;white-space:nowrap;width:96px}}
  .ag{{white-space:nowrap;width:56px;font-size:11px;text-transform:uppercase;letter-spacing:.05em}}
  .ag.claude{{color:#c084fc}}
  .ag.codex{{color:#22d3ee}}
  .ttl{{color:#e7e7ea}}
  .meta{{color:#55556a;white-space:nowrap;text-align:right}}
  .dot{{color:#3ddc84}}
  .row.live .ttl{{color:#fff}}
  #toast{{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#6366f1;color:#fff;padding:10px 18px;border-radius:8px;opacity:0;transition:.2s;pointer-events:none}}
  #toast.show{{opacity:1}}
</style>
<h1>Agent Sessions</h1>
<div class=sub>{len(rows)} active conversations (Claude + Codex) · {now} · ● = open now · ★ = named · click any row to copy its resume command</div>
{''.join(cards)}
<div id=toast>copied</div>
<script>
function cp(tr){{
  navigator.clipboard.writeText(tr.dataset.cmd);
  var t=document.getElementById('toast');t.textContent='copied: '+tr.dataset.cmd.slice(0,60)+'…';
  t.classList.add('show');setTimeout(()=>t.classList.remove('show'),1400);
}}
</script>"""
    (HERE / "dashboard.html").write_text(page)
    print(f"wrote {HERE / 'dashboard.html'}")
    print(f"wrote {HERE / 'dashboard.md'}")
    if "--open" in args:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        try:
            subprocess.Popen([opener, str(HERE / "dashboard.html")],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "list"
    rest = args[1:]
    if cmd in ("list", "ls"):
        cmd_list(rest)
    elif cmd == "pick":
        sys.exit(cmd_pick(rest) or 0)
    elif cmd == "_picklines":      # internal: fzf reload source
        print("\n".join(pick_lines()))
    elif cmd == "_rename":         # internal: fzf Ctrl-R handler
        cmd_rename_interactive(rest)
    elif cmd == "name":
        cmd_name(rest)
    elif cmd == "preview":
        cmd_preview(rest)
    elif cmd == "report":
        cmd_report(rest)
    elif cmd == "rebuild":
        build_index(force=True)
        print("index rebuilt")
    elif cmd in ("-h", "--help", "help"):
        print(__doc__)
    else:
        cmd_list(args)  # treat an unknown first arg as a list query


if __name__ == "__main__":
    main()
