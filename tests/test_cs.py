import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cs", ROOT / "cs.py")
cs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cs)


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


class CsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.state = self.home / "state"
        self.state.mkdir()

        cs.HOME = str(self.home)
        cs.PROJECTS = self.home / ".claude" / "projects"
        cs.SESSIONS_DIR = self.home / ".claude" / "sessions"
        cs.CODEX_SESSIONS = self.home / ".codex" / "sessions"
        cs.CODEX_INDEX = self.home / ".codex" / "session_index.jsonl"
        cs.CACHE = self.state / ".cache.json"
        cs.NAMES = self.state / "names.json"
        cs.PROJECT_ROOT = ""

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_claude_transcript(self):
        transcript = cs.PROJECTS / "-tmp-project" / "abc123.jsonl"
        write_jsonl(transcript, [
            {
                "type": "user",
                "cwd": str(self.home / "Code" / "project"),
                "timestamp": "2026-01-01T00:00:00Z",
                "lastPrompt": "Build the thing",
                "gitBranch": "main",
            },
            {
                "type": "assistant",
                "timestamp": "2026-01-01T00:01:00Z",
                "aiTitle": "Project setup",
                "customTitle": "Launch prep",
            },
        ])

        row = cs.parse_claude(transcript)

        self.assertEqual(row["sid"], "abc123")
        self.assertEqual(row["cwd"], str(self.home / "Code" / "project"))
        self.assertEqual(row["title"], "Project setup")
        self.assertEqual(row["custom_title"], "Launch prep")
        self.assertEqual(row["branch"], "main")
        self.assertEqual(row["msgs"], 1)
        self.assertEqual(row["agent"], "claude")

    def test_parse_codex_transcript(self):
        transcript = cs.CODEX_SESSIONS / "2026" / "01" / "01" / "rollout-2026-01-01T00-00-00-deadbeef.jsonl"
        write_jsonl(transcript, [
            {
                "type": "session_meta",
                "timestamp": "2026-01-01T00:00:00Z",
                "payload": {
                    "session_id": "codex-session",
                    "cwd": str(self.home / "Projects" / "api"),
                    "source": "cli",
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-01-01T00:01:00Z",
                "payload": {"type": "user_message", "message": "Fix the tests"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-01-01T00:02:00Z",
                "payload": {"type": "user_message", "message": "Run them again"},
            },
        ])

        row = cs.parse_codex(transcript)

        self.assertEqual(row["sid"], "codex-session")
        self.assertEqual(row["title"], "Fix the tests")
        self.assertEqual(row["last_prompt"], "Run them again")
        self.assertEqual(row["msgs"], 2)
        self.assertEqual(row["agent"], "codex")
        self.assertEqual(row["origin"], "cli")

    def test_build_index_applies_names_and_filters_exec_runs(self):
        claude = cs.PROJECTS / "-tmp-project" / "claude-session.jsonl"
        write_jsonl(claude, [
            {
                "type": "user",
                "cwd": str(self.home / "Code" / "web"),
                "timestamp": "2026-01-01T00:00:00Z",
                "lastPrompt": "Claude prompt",
                "aiTitle": "Claude title",
            }
        ])
        codex_cli = cs.CODEX_SESSIONS / "2026" / "01" / "01" / "rollout-2026-01-01T00-00-00-cli.jsonl"
        write_jsonl(codex_cli, [
            {
                "type": "session_meta",
                "timestamp": "2026-01-02T00:00:00Z",
                "payload": {
                    "session_id": "codex-cli",
                    "cwd": str(self.home / "Code" / "api"),
                    "source": "cli",
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-01-02T00:01:00Z",
                "payload": {"type": "user_message", "message": "Codex prompt"},
            },
        ])
        codex_exec = cs.CODEX_SESSIONS / "2026" / "01" / "01" / "rollout-2026-01-01T00-00-00-exec.jsonl"
        write_jsonl(codex_exec, [
            {
                "type": "session_meta",
                "timestamp": "2026-01-03T00:00:00Z",
                "payload": {
                    "session_id": "codex-exec",
                    "cwd": str(self.home / "Code" / "bot"),
                    "source": "exec",
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-01-03T00:01:00Z",
                "payload": {"type": "user_message", "message": "Automated run"},
            },
        ])
        write_jsonl(cs.CODEX_INDEX, [
            {"id": "codex-cli", "thread_name": "Named from Codex"}
        ])
        cs.NAMES.write_text(json.dumps({"claude-session": "Manual name"}))

        rows = cs.build_index(force=True)
        active_rows = cs.active(rows)

        self.assertEqual([row["sid"] for row in active_rows], ["codex-cli", "claude-session"])
        self.assertEqual(active_rows[0]["custom_title"], "Named from Codex")
        self.assertEqual(active_rows[1]["name"], "Manual name")
        self.assertEqual(cs.label_for(active_rows[0]), "Named from Codex")
        self.assertEqual(cs.label_for(active_rows[1]), "Manual name")

    def test_proj_label_is_generic_and_can_use_project_root(self):
        self.assertEqual(cs.proj_label(str(self.home / "Code" / "product" / "api")), "product/api")

        cs.PROJECT_ROOT = str(self.home / "workspace")
        self.assertEqual(
            cs.proj_label(str(self.home / "workspace" / "product" / ".worktrees" / "feature-a")),
            "product:feature-a",
        )
        self.assertEqual(
            cs.proj_label(str(self.home / "Documents" / "Codex" / "2026-01-01" / "scratch")),
            "codex-app",
        )


if __name__ == "__main__":
    unittest.main()
