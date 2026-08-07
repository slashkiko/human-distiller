from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "human-distiller"
CLI = PLUGIN_ROOT / "skills" / "human-distiller" / "scripts" / "human_distiller.py"
QUESTIONS = PLUGIN_ROOT / "skills" / "human-distiller" / "references" / "questions.json"


def run_cli(state_dir: Path, *arguments: str, env: dict[str, str] | None = None, expect: int = 0) -> dict:
    command = ["python3", str(CLI), "--state-dir", str(state_dir), *arguments]
    result = subprocess.run(command, cwd=REPO_ROOT, env=env, text=True, capture_output=True, check=False)
    if result.returncode != expect:
        raise AssertionError(f"{command} returned {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}")
    stream = result.stdout if expect == 0 else result.stderr
    return json.loads(stream)


class QuestionnaireTests(unittest.TestCase):
    def test_levels_have_expected_counts_and_prefixes(self) -> None:
        data = json.loads(QUESTIONS.read_text(encoding="utf-8"))
        self.assertEqual(4, len(data["levels"]["minimal"]))
        self.assertEqual(8, len(data["levels"]["standard"]))
        self.assertEqual(12, len(data["levels"]["broad"]))
        self.assertEqual(data["levels"]["minimal"], data["levels"]["standard"][:4])
        self.assertEqual(data["levels"]["standard"], data["levels"]["broad"][:8])

    def test_pause_and_resume_preserve_unknown_questions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            started = run_cli(state_dir, "start", "--level", "standard")
            self.assertEqual((8, 0, 8), (started["total"], started["answered"], started["remaining"]))

            first = started["next_question"]["id"]
            second_result = run_cli(state_dir, "answer", "--question-id", first, "--value", "Synthetic preference one")
            second = second_result["next_question"]["id"]
            run_cli(state_dir, "answer", "--question-id", second, "--value", "Synthetic preference two")
            paused = run_cli(state_dir, "pause")
            self.assertEqual(("paused", 2, 6), (paused["status"], paused["answered"], paused["remaining"]))

            raw_state = json.loads((state_dir / "onboarding-state.json").read_text(encoding="utf-8"))
            self.assertEqual({first, second}, set(raw_state["answers"]))
            resumed = run_cli(state_dir, "resume")
            self.assertEqual(raw_state["question_ids"][2], resumed["next_question"]["id"])
            self.assertNotIn(raw_state["question_ids"][2], (state_dir / "decision-profile.md").read_text(encoding="utf-8"))

    def test_secret_like_answer_is_rejected_without_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            started = run_cli(state_dir, "start", "--level", "minimal")
            question_id = started["next_question"]["id"]
            rejected = run_cli(
                state_dir,
                "answer",
                "--question-id",
                question_id,
                "--value",
                "sk-abcdefghijklmnopqrstuvwxyz012345",
                expect=2,
            )
            self.assertFalse(rejected["ok"])
            status = run_cli(state_dir, "status")
            self.assertEqual(0, status["answered"])


class InstallationTests(unittest.TestCase):
    def complete_minimal(self, state_dir: Path) -> None:
        result = run_cli(state_dir, "start", "--level", "minimal")
        counter = 1
        while result["next_question"] is not None:
            result = run_cli(
                state_dir,
                "answer",
                "--question-id",
                result["next_question"]["id"],
                "--value",
                f"Synthetic answer {counter}",
            )
            counter += 1

    def test_codex_and_claude_install_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_dir = root / "state"
            self.complete_minimal(state_dir)
            env = os.environ.copy()
            env["HOME"] = str(root / "home")
            env["CODEX_HOME"] = str(root / "home" / ".codex")
            env["CLAUDE_CONFIG_DIR"] = str(root / "home" / ".claude")

            first_codex = run_cli(state_dir, "install", "--product", "codex", env=env)
            second_codex = run_cli(state_dir, "install", "--product", "codex", env=env)
            self.assertTrue(first_codex["changed"])
            self.assertTrue(second_codex["idempotent"])
            agents_text = (root / "home" / ".codex" / "AGENTS.md").read_text(encoding="utf-8")
            self.assertEqual(1, agents_text.count("<!-- human-distiller:start -->"))
            self.assertTrue((root / "home" / ".codex" / "agents" / "trusted-delegate.toml").exists())

            first_claude = run_cli(state_dir, "install", "--product", "claude", env=env)
            second_claude = run_cli(state_dir, "install", "--product", "claude", env=env)
            self.assertTrue(first_claude["changed"])
            self.assertTrue(second_claude["idempotent"])
            claude_text = (root / "home" / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertEqual(1, claude_text.count("<!-- human-distiller:start -->"))
            validation = run_cli(state_dir, "validate")
            self.assertTrue(validation["valid"], validation["errors"])

            (root / "home" / ".codex" / "agents" / "trusted-delegate.toml").write_text("tampered\n", encoding="utf-8")
            invalid = run_cli(state_dir, "validate")
            self.assertFalse(invalid["valid"])
            self.assertTrue(any("does not match" in error for error in invalid["errors"]))

    def test_migration_uses_only_recognized_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            self.complete_minimal(source)
            (source / "ignore-me.txt").write_text("synthetic", encoding="utf-8")
            migrated = run_cli(destination, "migrate", "--source-dir", str(source))
            self.assertTrue(migrated["validation"]["valid"])
            self.assertFalse((destination / "ignore-me.txt").exists())

    def test_invalid_migration_does_not_replace_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            self.complete_minimal(destination)
            original = (destination / "onboarding-state.json").read_text(encoding="utf-8")
            source.mkdir()
            (source / "onboarding-state.json").write_text('{"level":"not-a-level"}\n', encoding="utf-8")
            rejected = run_cli(destination, "migrate", "--source-dir", str(source), expect=2)
            self.assertFalse(rejected["ok"])
            self.assertEqual(original, (destination / "onboarding-state.json").read_text(encoding="utf-8"))

    def test_journal_stats_and_active_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            self.complete_minimal(state_dir)
            for index in range(3):
                run_cli(
                    state_dir,
                    "record",
                    "--source",
                    "escalation_answer",
                    "--context",
                    f"Synthetic context {index}",
                    "--option",
                    "Option A",
                    "--option",
                    "Option B",
                    "--choice",
                    "Option A",
                )
            stats = run_cli(state_dir, "stats")
            self.assertEqual(3, stats["review_count"])
            self.assertTrue(stats["distillation_review_due"])

            rule = run_cli(
                state_dir,
                "set-rule",
                "--rule-id",
                "synthetic-rule",
                "--status",
                "active",
                "--scope",
                "Synthetic local choices",
                "--preference",
                "Prefer option A",
                "--evidence",
                "journal:1",
            )
            self.assertEqual("active", rule["rule"]["status"])
            profile = (state_dir / "decision-profile.md").read_text(encoding="utf-8")
            self.assertIn("synthetic-rule", profile)
            self.assertIn("Prefer option A", profile)


class RepositoryTests(unittest.TestCase):
    def test_manifests_share_name_and_version(self) -> None:
        codex = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        claude = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        marketplace = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual("human-distiller", codex["name"])
        self.assertEqual(codex["name"], claude["name"])
        self.assertEqual(codex["version"], claude["version"])
        self.assertEqual(codex["version"], marketplace["plugins"][0]["version"])

    def test_repository_contains_no_known_personal_home_path(self) -> None:
        forbidden = ("/Users/" + "shota", "/Users/" + "sanoshota")
        for path in REPO_ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for value in forbidden:
                self.assertNotIn(value, text, f"personal path found in {path.relative_to(REPO_ROOT)}")

    def test_personal_state_files_are_not_at_repository_root(self) -> None:
        for name in ("decision-profile.md", "decision-journal.jsonl", "onboarding-state.json", "install-state.json"):
            self.assertFalse((REPO_ROOT / name).exists())

    def test_mutating_command_rejects_state_inside_git_worktree(self) -> None:
        state_dir = REPO_ROOT / "nested" / "private-state"
        rejected = run_cli(state_dir, "start", "--level", "minimal", expect=2)
        self.assertFalse(rejected["ok"])
        self.assertIn("must not be inside a Git repository", rejected["error"])
        self.assertFalse(state_dir.exists())


if __name__ == "__main__":
    unittest.main()
