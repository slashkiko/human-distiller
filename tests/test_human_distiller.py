from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import stat
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



def complete_onboarding(state_dir: Path) -> None:
    result = run_cli(state_dir, "start", "--risk-ceiling", "low")
    counter = 1
    while result["next_question"] is not None:
        question = result["next_question"]["id"]
        result = run_cli(state_dir, "answer", "--question-id", question, "--value", f"Synthetic answer {counter}")
        counter += 1


def snapshot_tree(root: Path, exclude: Path | None = None) -> dict[Path, tuple]:
    """Record every path under root without following symlinks: type, mode, inode, link count, link target, and file content."""
    entries: dict[Path, tuple] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if Path(dirpath) / name != exclude]
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                entries[path] = ("link", os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                entries[path] = ("dir", stat.S_IMODE(info.st_mode), info.st_ino)
            else:
                entries[path] = ("file", stat.S_IMODE(info.st_mode), info.st_ino, info.st_nlink, path.read_bytes())
    return entries


def cli_options() -> dict[str, set[str]]:
    """Map each subcommand to its option strings, read from the CLI's own parser."""
    spec = importlib.util.spec_from_file_location("human_distiller_cli", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parser = module.build_parser()
    subparsers = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    return {
        name: {option for action in sub._actions for option in action.option_strings if option not in ("-h", "--help")}
        for name, sub in subparsers.choices.items()
    }


def untracked_files() -> set[str] | None:
    """List untracked and ignored files in the repository, apart from bytecode caches."""
    if not (REPO_ROOT / ".git").exists():
        return None
    result = subprocess.run(["git", "ls-files", "--others"], cwd=REPO_ROOT, text=True, capture_output=True, check=True)
    return {line for line in result.stdout.splitlines() if "__pycache__" not in line}


UNTRACKED_AT_START: set[str] | None = None


def setUpModule() -> None:
    global UNTRACKED_AT_START
    UNTRACKED_AT_START = untracked_files()


def tearDownModule() -> None:
    # A test run must not leave files in the repository, such as a CLI writing into the plugin directory.
    left = (untracked_files() or set()) - (UNTRACKED_AT_START or set())
    if left:
        raise AssertionError(f"Tests left files in the repository: {sorted(left)}")


class QuestionnaireTests(unittest.TestCase):
    def test_risk_levels_have_equal_counts_and_distinct_content(self) -> None:
        data = json.loads(QUESTIONS.read_text(encoding="utf-8"))
        levels = data["risk_levels"]
        self.assertEqual({"low", "medium", "high"}, set(levels))
        self.assertEqual({8}, {len(question_ids) for question_ids in levels.values()})
        common = levels["low"][:4]
        self.assertEqual(common, levels["medium"][:4])
        self.assertEqual(common, levels["high"][:4])
        self.assertNotEqual(levels["low"][4:], levels["medium"][4:])
        self.assertNotEqual(levels["medium"][4:], levels["high"][4:])

    def test_pause_and_resume_preserve_unknown_questions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            started = run_cli(state_dir, "start", "--risk-ceiling", "medium")
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
            started = run_cli(state_dir, "start", "--risk-ceiling", "low")
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
    def test_codex_and_claude_install_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_dir = root / "state"
            complete_onboarding(state_dir)
            env = os.environ.copy()
            env["HOME"] = str(root / "home")
            env["CODEX_HOME"] = str(root / "home" / ".codex")
            env["CLAUDE_CONFIG_DIR"] = str(root / "home" / ".claude")

            legacy_agents = root / "home" / ".codex" / "agents"
            legacy_agents.mkdir(parents=True)
            (legacy_agents / "trusted-delegate.toml").write_text(
                'name = "trusted_delegate"\ndeveloper_instructions = "Read ~/.human-distiller/decision-profile.md"\n',
                encoding="utf-8",
            )
            (legacy_agents / "preference-distiller.toml").write_text(
                'name = "preference_distiller"\ndeveloper_instructions = "Read ~/.human-distiller/decision-profile.md"\n',
                encoding="utf-8",
            )

            first_codex = run_cli(state_dir, "install", "--product", "codex", env=env)
            second_codex = run_cli(state_dir, "install", "--product", "codex", env=env)
            self.assertTrue(first_codex["changed"])
            self.assertEqual(2, len(first_codex["removed"]))
            self.assertFalse(first_codex["legacy_conflicts"])
            self.assertGreaterEqual(len(first_codex["backups"]), 2)
            self.assertTrue(second_codex["idempotent"])
            agents_text = (root / "home" / ".codex" / "AGENTS.md").read_text(encoding="utf-8")
            self.assertEqual(1, agents_text.count("<!-- human-distiller:start -->"))
            self.assertTrue((root / "home" / ".codex" / "agents" / "alter-ego.toml").exists())
            self.assertTrue((root / "home" / ".codex" / "agents" / "decision-distiller.toml").exists())
            self.assertFalse((root / "home" / ".codex" / "agents" / "trusted-delegate.toml").exists())
            self.assertFalse((root / "home" / ".codex" / "agents" / "preference-distiller.toml").exists())

            first_claude = run_cli(state_dir, "install", "--product", "claude", env=env)
            second_claude = run_cli(state_dir, "install", "--product", "claude", env=env)
            self.assertTrue(first_claude["changed"])
            self.assertTrue(second_claude["idempotent"])
            claude_text = (root / "home" / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertEqual(1, claude_text.count("<!-- human-distiller:start -->"))
            validation = run_cli(state_dir, "validate")
            self.assertTrue(validation["valid"], validation["errors"])

            (root / "home" / ".codex" / "agents" / "alter-ego.toml").write_text("tampered\n", encoding="utf-8")
            invalid = run_cli(state_dir, "validate")
            self.assertFalse(invalid["valid"])
            self.assertTrue(any("does not match" in error for error in invalid["errors"]))

    def test_install_creates_destination_of_dangling_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_dir = root / "state"
            complete_onboarding(state_dir)
            env = os.environ.copy()
            env["HOME"] = str(root / "home")
            env["CLAUDE_CONFIG_DIR"] = str(root / "home" / ".claude")
            link = root / "home" / ".claude" / "CLAUDE.md"
            real = root / "dotfiles" / "claude" / "CLAUDE.md"
            link.parent.mkdir(parents=True)
            link.symlink_to(real)

            run_cli(state_dir, "install", "--product", "claude", env=env)

            self.assertTrue(link.is_symlink())
            self.assertEqual(1, real.read_text(encoding="utf-8").count("<!-- human-distiller:start -->"))
            self.assertEqual(0o700, real.parent.stat().st_mode & 0o777)
            self.assertEqual(0o700, real.parent.parent.stat().st_mode & 0o777)
            self.assertEqual(0o600, real.stat().st_mode & 0o777)

            link.unlink()
            link.mkdir()
            rejected = run_cli(state_dir, "install", "--product", "claude", env=env, expect=2)
            self.assertFalse(rejected["ok"])

    @unittest.skipIf(os.geteuid() == 0, "root bypasses directory permissions")
    def test_install_rejects_symlink_loop_and_unwritable_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_dir = root / "state"
            complete_onboarding(state_dir)
            env = os.environ.copy()
            env["HOME"] = str(root / "home")
            env["CLAUDE_CONFIG_DIR"] = str(root / "home" / ".claude")
            env["CODEX_HOME"] = str(root / "home" / ".codex")
            loop = root / "home" / ".claude" / "CLAUDE.md"
            loop.parent.mkdir(parents=True)
            loop.symlink_to(loop.parent / "other.md")
            (loop.parent / "other.md").symlink_to(loop)
            agents_md = root / "home" / ".codex" / "AGENTS.md"
            agents_md.parent.mkdir(parents=True)
            read_only = root / "read-only"
            read_only.mkdir()
            (read_only / "AGENTS.md").write_text("# Managed elsewhere\n", encoding="utf-8")
            agents_md.symlink_to(read_only / "AGENTS.md")
            read_only.chmod(0o555)
            try:
                rejected_loop = run_cli(state_dir, "install", "--product", "claude", env=env, expect=2)
                rejected_codex = run_cli(state_dir, "install", "--product", "codex", env=env, expect=2)
            finally:
                read_only.chmod(0o755)

            self.assertIn("symlink loop", rejected_loop["error"])
            self.assertTrue(loop.is_symlink())
            self.assertIn("not a writable directory", rejected_codex["error"])
            self.assertFalse((root / "home" / ".codex" / "agents").exists())
            self.assertEqual("# Managed elsewhere\n", (read_only / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertFalse((state_dir / "backups").exists())

    @unittest.skipIf(os.geteuid() == 0, "root bypasses directory permissions")
    def test_install_and_uninstall_check_every_target_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_dir = root / "state"
            complete_onboarding(state_dir)
            env = os.environ.copy()
            env["HOME"] = str(root / "home")
            env["CLAUDE_CONFIG_DIR"] = str(root / "home" / ".claude")
            env["CODEX_HOME"] = str(root / "home" / ".codex")
            codex_home = root / "home" / ".codex"
            codex_home.mkdir(parents=True)
            managed = root / "managed"
            managed.mkdir()
            (codex_home / "AGENTS.md").symlink_to(managed / "AGENTS.md")
            run_cli(state_dir, "install", "--product", "codex", env=env)
            managed.chmod(0o555)
            try:
                self.assertTrue(run_cli(state_dir, "install", "--product", "codex", env=env)["idempotent"])
                rejected = run_cli(state_dir, "uninstall", "--product", "codex", env=env, expect=2)
            finally:
                managed.chmod(0o755)
            self.assertIn("not a writable directory", rejected["error"])
            self.assertTrue((codex_home / "agents" / "alter-ego.toml").exists())
            self.assertTrue((codex_home / "agents" / "decision-distiller.toml").exists())

            run_cli(state_dir, "uninstall", "--product", "codex", env=env)
            marker = "<!-- human-distiller:start -->\n"
            (managed / "AGENTS.md").write_text(marker + marker, encoding="utf-8")
            malformed = run_cli(state_dir, "install", "--product", "codex", env=env, expect=2)
            self.assertIn("malformed", malformed["error"])
            self.assertFalse((codex_home / "agents" / "alter-ego.toml").exists())

            claude_md = root / "home" / ".claude" / "CLAUDE.md"
            claude_md.parent.mkdir(parents=True)
            claude_md.write_bytes(b"\xff\xfe not utf-8\n")
            undecodable = run_cli(state_dir, "install", "--product", "claude", env=env, expect=2)
            self.assertIn("UTF-8", undecodable["error"])
            self.assertEqual(b"\xff\xfe not utf-8\n", claude_md.read_bytes())

            claude_md.write_text("# Locked\n", encoding="utf-8")
            claude_md.chmod(0o444)
            locked = run_cli(state_dir, "install", "--product", "claude", env=env, expect=2)
            self.assertIn("read-only", locked["error"])
            self.assertEqual("# Locked\n", claude_md.read_text(encoding="utf-8"))

            (managed / "AGENTS.md").write_text("# Codex guidance\n", encoding="utf-8")
            agents_dir = codex_home / "agents"
            (agents_dir / "preference-distiller.toml").write_bytes(b"\xff not utf-8\n")
            (agents_dir / "decision-distiller.toml").symlink_to(root / "missing.toml")
            installed = run_cli(state_dir, "install", "--product", "codex", env=env)
            self.assertIn(str(agents_dir / "preference-distiller.toml"), installed["legacy_conflicts"])
            self.assertIn(str(agents_dir / "decision-distiller.toml"), installed["conflicts"])
            uninstalled = run_cli(state_dir, "uninstall", "--product", "codex", env=env)
            self.assertIn(str(agents_dir / "decision-distiller.toml"), uninstalled["conflicts"])
            self.assertTrue((agents_dir / "decision-distiller.toml").is_symlink())

    def test_private_writes_refuse_symlinks(self) -> None:
        for linked in ("decision-profile.md", "backups"):
            with self.subTest(linked=linked), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                state_dir = root / "state"
                complete_onboarding(state_dir)
                env = os.environ.copy()
                env["HOME"] = str(root / "home")
                env["CLAUDE_CONFIG_DIR"] = str(root / "home" / ".claude")
                outside = root / "outside"
                outside.mkdir(mode=0o755)
                outside.chmod(0o755)
                if linked == "backups":
                    run_cli(state_dir, "install", "--product", "claude", env=env)
                    (root / "home" / ".claude" / "CLAUDE.md").write_text("# Edited\n", encoding="utf-8")
                    shutil.rmtree(state_dir / "backups", ignore_errors=True)
                    arguments = ("install", "--product", "claude")
                else:
                    (outside / "decision-profile.md").write_text("user file\n", encoding="utf-8")
                    (state_dir / "decision-profile.md").unlink()
                    arguments = ("set-rule", "--rule-id", "synthetic-rule", "--status", "candidate",
                                 "--scope", "Synthetic scope", "--preference", "Synthetic preference")
                (state_dir / linked).symlink_to(outside if linked == "backups" else outside / linked)
                before = snapshot_tree(outside)
                state_before = (state_dir / "onboarding-state.json").read_bytes()

                rejected = run_cli(state_dir, *arguments, env=env, expect=2)

                self.assertIn("through a symlink", rejected["error"])
                self.assertTrue((state_dir / linked).is_symlink())
                self.assertEqual(before, snapshot_tree(outside))
                self.assertEqual(state_before, (state_dir / "onboarding-state.json").read_bytes())
                self.assertEqual(0o755, outside.stat().st_mode & 0o777)

    def test_migration_uses_only_recognized_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            complete_onboarding(source)
            (source / "ignore-me.txt").write_text("synthetic", encoding="utf-8")
            migrated = run_cli(destination, "migrate", "--source-dir", str(source))
            self.assertTrue(migrated["validation"]["valid"])
            self.assertFalse((destination / "ignore-me.txt").exists())

    def test_unrecognized_legacy_agent_is_preserved_and_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_dir = root / "state"
            complete_onboarding(state_dir)
            env = os.environ.copy()
            env["HOME"] = str(root / "home")
            env["CODEX_HOME"] = str(root / "home" / ".codex")
            agents_dir = root / "home" / ".codex" / "agents"
            agents_dir.mkdir(parents=True)
            legacy = agents_dir / "trusted-delegate.toml"
            legacy.write_text('name = "trusted_delegate"\ndeveloper_instructions = "Custom unrelated agent"\n', encoding="utf-8")

            installed = run_cli(state_dir, "install", "--product", "codex", env=env)
            self.assertTrue(legacy.exists())
            self.assertIn(str(legacy), installed["legacy_conflicts"])
            self.assertNotIn(str(legacy), installed["removed"])
            self.assertTrue((agents_dir / "alter-ego.toml").exists())

    def test_uninstall_removes_owned_adapters_and_preserves_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_dir = root / "state"
            complete_onboarding(state_dir)
            profile_before = (state_dir / "decision-profile.md").read_text(encoding="utf-8")
            env = os.environ.copy()
            env["HOME"] = str(root / "home")
            env["CODEX_HOME"] = str(root / "home" / ".codex")
            env["CLAUDE_CONFIG_DIR"] = str(root / "home" / ".claude")
            codex_home = root / "home" / ".codex"
            claude_home = root / "home" / ".claude"
            codex_home.mkdir(parents=True)
            claude_home.mkdir(parents=True)
            (codex_home / "AGENTS.md").write_text("# Existing Codex guidance\n", encoding="utf-8")
            (claude_home / "CLAUDE.md").write_text("# Existing Claude guidance\n", encoding="utf-8")

            run_cli(state_dir, "install", "--product", "codex", env=env)
            run_cli(state_dir, "install", "--product", "claude", env=env)
            codex_result = run_cli(state_dir, "uninstall", "--product", "codex", env=env)
            claude_result = run_cli(state_dir, "uninstall", "--product", "claude", env=env)

            self.assertEqual(3, len(codex_result["removed"]))
            self.assertEqual(1, len(claude_result["removed"]))
            self.assertFalse(codex_result["conflicts"])
            self.assertFalse(claude_result["conflicts"])
            self.assertFalse((codex_home / "agents" / "alter-ego.toml").exists())
            self.assertFalse((codex_home / "agents" / "decision-distiller.toml").exists())
            self.assertEqual("# Existing Codex guidance\n", (codex_home / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertEqual("# Existing Claude guidance\n", (claude_home / "CLAUDE.md").read_text(encoding="utf-8"))
            self.assertEqual(profile_before, (state_dir / "decision-profile.md").read_text(encoding="utf-8"))
            self.assertEqual([], run_cli(state_dir, "status")["installed_products"])
            self.assertTrue(run_cli(state_dir, "uninstall", "--product", "codex", env=env)["idempotent"])

    def test_uninstall_preserves_modified_adapter_as_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_dir = root / "state"
            complete_onboarding(state_dir)
            env = os.environ.copy()
            env["HOME"] = str(root / "home")
            env["CODEX_HOME"] = str(root / "home" / ".codex")
            env["CLAUDE_CONFIG_DIR"] = str(root / "home" / ".claude")
            run_cli(state_dir, "install", "--product", "codex", env=env)
            run_cli(state_dir, "install", "--product", "claude", env=env)
            adapter = root / "home" / ".codex" / "agents" / "alter-ego.toml"
            adapter.write_text("customized\n", encoding="utf-8")
            agents_file = root / "home" / ".codex" / "AGENTS.md"
            agents_file.write_text(
                "<!-- human-distiller:start -->\ncustomized Codex section\n<!-- human-distiller:end -->\n",
                encoding="utf-8",
            )
            claude_file = root / "home" / ".claude" / "CLAUDE.md"
            claude_file.write_text(
                "<!-- human-distiller:start -->\ncustomized Claude section\n<!-- human-distiller:end -->\n",
                encoding="utf-8",
            )

            codex_result = run_cli(state_dir, "uninstall", "--product", "codex", env=env)
            claude_result = run_cli(state_dir, "uninstall", "--product", "claude", env=env)

            self.assertTrue(adapter.exists())
            self.assertIn(str(adapter), codex_result["conflicts"])
            self.assertIn(str(agents_file), codex_result["conflicts"])
            self.assertIn(str(claude_file), claude_result["conflicts"])
            self.assertFalse((adapter.parent / "decision-distiller.toml").exists())

    def test_invalid_migration_does_not_replace_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            complete_onboarding(destination)
            original = (destination / "onboarding-state.json").read_text(encoding="utf-8")
            source.mkdir()
            (source / "onboarding-state.json").write_text('{"risk_ceiling":"not-a-level"}\n', encoding="utf-8")
            rejected = run_cli(destination, "migrate", "--source-dir", str(source), expect=2)
            self.assertFalse(rejected["ok"])
            self.assertEqual(original, (destination / "onboarding-state.json").read_text(encoding="utf-8"))

    def test_journal_stats_and_active_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            complete_onboarding(state_dir)
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

    def test_v1_state_requires_explicit_risk_ceiling_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            state_dir.mkdir(mode=0o700)
            legacy = {
                "schema_version": 1,
                "question_set_version": "legacy-depth-v1",
                "level": "standard",
                "status": "paused",
                "question_ids": ["delegation_areas", "hard_boundaries", "reversibility", "reporting"],
                "answers": {
                    "delegation_areas": "Synthetic legacy answer",
                    "hard_boundaries": "Synthetic legacy boundary",
                },
                "rules": [
                    {
                        "id": "legacy-rule",
                        "status": "active",
                        "scope": "Synthetic scope",
                        "preference": "Synthetic preference",
                        "reason": "Synthetic reason",
                        "evidence": ["legacy:1"],
                        "last_confirmed": "2026-01-01T00:00:00+00:00",
                    }
                ],
            }
            (state_dir / "onboarding-state.json").write_text(json.dumps(legacy), encoding="utf-8")
            (state_dir / "decision-profile.md").write_text("Synthetic legacy profile\n", encoding="utf-8")
            journal_text = '{"schema_version":1,"source":"user_choice","choice":"Synthetic"}\n'
            (state_dir / "decision-journal.jsonl").write_text(journal_text, encoding="utf-8")
            for path in state_dir.iterdir():
                path.chmod(0o600)

            status = run_cli(state_dir, "status")
            self.assertTrue(status["migration_required"])
            self.assertEqual("standard", status["legacy_level"])
            self.assertIsNone(status["risk_ceiling"])
            resume = run_cli(state_dir, "resume", expect=2)
            self.assertIn("requires migration", resume["error"])

            migrated = run_cli(state_dir, "migrate-v1", "--risk-ceiling", "medium")
            self.assertEqual("medium", migrated["risk_ceiling"])
            self.assertEqual(8, migrated["total"])
            self.assertEqual(0, migrated["answered"])
            self.assertEqual(2, migrated["legacy_answers_preserved_in_backup"])
            self.assertEqual(1, migrated["rules_preserved"])
            self.assertEqual(journal_text, (state_dir / "decision-journal.jsonl").read_text(encoding="utf-8"))

            current = json.loads((state_dir / "onboarding-state.json").read_text(encoding="utf-8"))
            self.assertEqual({}, current["answers"])
            self.assertEqual("legacy-rule", current["rules"][0]["id"])
            self.assertTrue(migrated["backups"])
            self.assertTrue(run_cli(state_dir, "validate")["valid"])



class UserFilesystemInvariantTests(unittest.TestCase):
    """Outside the state directory, no command may replace a symlink, change a mode, or touch an undeclared path."""

    LAYOUTS = ("regular", "file-links", "dir-links")
    OWNED_LEGACY = 'name = "trusted_delegate"\ndeveloper_instructions = "Read ~/.human-distiller/decision-profile.md"\n'
    FOREIGN_LEGACY = 'name = "preference_distiller"\ndeveloper_instructions = "Unrelated agent"\n'

    def build_home(self, root: Path, layout: str) -> dict[str, Path]:
        home = root / "home"
        dotfiles = root / "dotfiles"
        home.mkdir(mode=0o755)
        dotfiles.mkdir(mode=0o755)

        def directory(path: Path) -> Path:
            path.mkdir()
            path.chmod(0o755)
            return path

        def file(path: Path, text: str) -> Path:
            path.write_text(text, encoding="utf-8")
            path.chmod(0o644)
            return path

        contents = {
            "CLAUDE.md": "# Existing Claude guidance\n",
            "AGENTS.md": "# Existing Codex guidance\n",
            "alter-ego.toml": "customized\n",
            "trusted-delegate.toml": self.OWNED_LEGACY,
            "preference-distiller.toml": self.FOREIGN_LEGACY,
        }
        if layout == "file-links":
            # A link whose content already matches the adapter: install leaves it, uninstall must not unlink it.
            adapter = PLUGIN_ROOT / "adapters" / "codex" / "decision-distiller.toml"
            contents["decision-distiller.toml"] = adapter.read_text(encoding="utf-8")
        if layout == "dir-links":
            claude, codex = home / ".claude", home / ".codex"
            claude.symlink_to(directory(dotfiles / "claude"))
            codex.symlink_to(directory(dotfiles / "codex"))
            (codex / "agents").symlink_to(directory(dotfiles / "agents"))
        else:
            claude, codex = directory(home / ".claude"), directory(home / ".codex")
            directory(codex / "agents")
        agents = codex / "agents"
        for name, text in contents.items():
            target = (claude if name == "CLAUDE.md" else codex if name == "AGENTS.md" else agents) / name
            if layout == "file-links":
                target.symlink_to(file(dotfiles / name, text))
            else:
                file(target, text)
        return {"home": home, "claude": claude, "codex": codex, "agents": agents}

    def write_v1_state(self, state_dir: Path) -> None:
        state_dir.mkdir(mode=0o700)
        legacy = {
            "schema_version": 1,
            "question_set_version": "legacy-depth-v1",
            "level": "standard",
            "status": "paused",
            "question_ids": ["delegation_areas", "hard_boundaries", "reversibility", "reporting"],
            "answers": {"delegation_areas": "Synthetic legacy answer"},
            "rules": [],
        }
        (state_dir / "onboarding-state.json").write_text(json.dumps(legacy), encoding="utf-8")
        (state_dir / "decision-profile.md").write_text("Synthetic legacy profile\n", encoding="utf-8")
        (state_dir / "decision-journal.jsonl").write_text("", encoding="utf-8")
        for path in state_dir.iterdir():
            path.chmod(0o600)

    def assert_confined(self, label: str, before: dict, after: dict, writes: set[Path], removes: set[Path]) -> None:
        for path, entry in before.items():
            if path not in after:
                self.assertIn(path, removes, f"{label} removed {path}")
            elif path in writes and entry[0] == after[path][0] == "file":
                self.assertEqual(entry[1], after[path][1], f"{label} changed the mode of {path}")
                if entry[4] != after[path][4]:
                    self.assertNotEqual(entry[2], after[path][2], f"{label} rewrote {path} in place")
            else:
                self.assertEqual(entry, after[path], f"{label} changed {path}")
        for path in after.keys() - before.keys():
            self.assertIn(path, writes, f"{label} created {path}")
            if after[path][0] == "file":
                self.assertEqual(0o600, after[path][1], f"{label} created {path} with a broad mode")

    def test_every_command_leaves_user_files_intact(self) -> None:
        for layout in self.LAYOUTS:
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as temporary:
                self.run_lifecycle(Path(os.path.realpath(temporary)), layout)

    def run_lifecycle(self, root: Path, layout: str) -> None:
        paths = self.build_home(root, layout)
        states = root / "states"
        states.mkdir(mode=0o700)
        state_dir = states / "state"
        env = os.environ.copy()
        env["HOME"] = str(paths["home"])
        env["CLAUDE_CONFIG_DIR"] = str(paths["claude"])
        env["CODEX_HOME"] = str(paths["codex"])

        def resolved(path: Path) -> Path:
            return Path(os.path.realpath(path))

        def unlinked(path: Path) -> Path:
            return resolved(path.parent) / path.name

        claude_md = paths["claude"] / "CLAUDE.md"
        agents_md = paths["codex"] / "AGENTS.md"
        alter_ego = paths["agents"] / "alter-ego.toml"
        agent_files = [alter_ego, paths["agents"] / "decision-distiller.toml"]
        owned_legacy = paths["agents"] / "trusted-delegate.toml"
        foreign_legacy = paths["agents"] / "preference-distiller.toml"
        # A symlinked whole-file adapter is reported as a conflict instead of written through.
        codex_writes = {resolved(agents_md), *(resolved(path) for path in agent_files if not path.is_symlink())}
        original = {path: resolved(path).read_text(encoding="utf-8") for path in (claude_md, agents_md)}
        options = cli_options()
        used: dict[str, set[str]] = {}

        def plugin_tree() -> dict:
            return {path: entry for path, entry in snapshot_tree(PLUGIN_ROOT).items() if "__pycache__" not in path.parts}

        def step(
            directory: Path, *arguments: str, writes: set[Path] = frozenset(), removes: set[Path] = frozenset()
        ) -> dict:
            before, plugin_before = snapshot_tree(root, states), plugin_tree()
            result = run_cli(directory, *arguments, env=env)
            label = f"{layout}: {' '.join(arguments[:3])}"
            self.assert_confined(label, before, snapshot_tree(root, states), writes, removes)
            self.assertEqual(plugin_before, plugin_tree(), f"{label} wrote into the plugin directory")
            used.setdefault(arguments[0], set()).update(item for item in arguments[1:] if item.startswith("--"))
            return result

        step(state_dir, "start", "--risk-ceiling", "low")
        result = step(state_dir, "start", "--risk-ceiling", "low", "--restart")
        result = step(state_dir, "answer", "--question-id", result["next_question"]["id"], "--value", "Synthetic 0")

        legacy_removes = set() if layout == "file-links" else {unlinked(owned_legacy)}
        installed = step(
            state_dir, "install", "--product", "codex", "--allow-incomplete",
            writes=codex_writes, removes=legacy_removes,
        )
        self.assertEqual(layout == "file-links", owned_legacy.is_symlink())
        self.assertEqual(layout == "file-links", str(owned_legacy) in installed["legacy_conflicts"])
        self.assertIn(str(foreign_legacy), installed["legacy_conflicts"])
        self.assertEqual(layout == "file-links", str(alter_ego) in installed["conflicts"])
        backup = next(item for item in installed["backups"] if Path(item).name == "AGENTS.md")
        self.assertEqual(original[agents_md], Path(backup).read_text(encoding="utf-8"))

        step(state_dir, "pause")
        result = step(state_dir, "resume")
        counter = 1
        while result["next_question"] is not None:
            question = result["next_question"]["id"]
            result = step(state_dir, "answer", "--question-id", question, "--value", f"Synthetic {counter}")
            counter += 1
        step(state_dir, "status")

        self.assertTrue(step(state_dir, "install", "--product", "codex", writes=codex_writes)["idempotent"])
        step(state_dir, "install", "--product", "claude", writes={resolved(claude_md)})
        self.assertTrue(step(state_dir, "install", "--product", "claude")["idempotent"])
        self.assertEqual(layout != "file-links", step(state_dir, "validate")["valid"])
        step(
            state_dir, "record", "--source", "correction", "--context", "Synthetic context",
            "--option", "Option A", "--option", "Option B", "--choice", "Option B",
            "--reason", "Synthetic reason", "--outcome", "Synthetic outcome",
            "--prediction", "Option A", "--prediction-confidence", "medium",
        )
        step(state_dir, "stats")
        step(
            state_dir, "set-rule", "--rule-id", "synthetic-rule", "--status", "candidate",
            "--scope", "Synthetic scope", "--preference", "Synthetic preference",
            "--reason", "Synthetic reason", "--evidence", "journal:1",
        )
        step(states / "copy", "migrate", "--source-dir", str(state_dir))

        installed_claude = resolved(claude_md).read_text(encoding="utf-8")
        edited = installed_claude.replace("<!-- human-distiller:end -->", "Edited by hand\n<!-- human-distiller:end -->")
        resolved(claude_md).write_text(edited, encoding="utf-8")
        conflicted = step(state_dir, "uninstall", "--product", "claude")
        self.assertIn(str(claude_md), conflicted["conflicts"])
        resolved(claude_md).write_text(installed_claude, encoding="utf-8")

        agent_removes = {unlinked(path) for path in agent_files if not path.is_symlink()}
        uninstalled = step(
            state_dir, "uninstall", "--product", "codex", writes={resolved(agents_md)}, removes=agent_removes
        )
        for path in agent_files:
            self.assertEqual(layout == "file-links", str(path) in uninstalled["conflicts"])
        step(state_dir, "uninstall", "--product", "claude", writes={resolved(claude_md)})
        for path, text in original.items():
            self.assertEqual(text, resolved(path).read_text(encoding="utf-8"))

        self.write_v1_state(states / "legacy")
        step(states / "legacy", "migrate-v1", "--risk-ceiling", "low")

        self.assertEqual(set(options), set(used))
        self.assertEqual(options, used)


class RepositoryTests(unittest.TestCase):
    def test_product_specific_skill_entrypoints_document_primary_actions(self) -> None:
        skill_root = PLUGIN_ROOT / "skills" / "human-distiller"
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        metadata = (skill_root / "agents" / "openai.yaml").read_text(encoding="utf-8")
        claude_entrypoint = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for invocation in (
            "$human-distiller init",
            "$human-distiller uninstall",
            "/human-distiller:human-distiller init",
            "/human-distiller:human-distiller uninstall",
        ):
            self.assertIn(invocation, skill)
        self.assertIn("$human-distiller init", metadata)
        self.assertIn("$human-distiller uninstall", metadata)
        self.assertIn('argument-hint: "[init|uninstall]"', claude_entrypoint)
        self.assertIn("plugins/human-distiller/skills/human-distiller/SKILL.md", claude_entrypoint)

    def test_adapters_document_when_and_how_to_record(self) -> None:
        skill = (PLUGIN_ROOT / "skills" / "human-distiller" / "SKILL.md").read_text(encoding="utf-8")
        recording_section = skill[skill.index("## Recording and drift") :]
        action = recording_section[recording_section.index("`record` action") :].split("\n\n", 1)[0]
        record_help = subprocess.run(
            ["python3", str(CLI), "record", "--help"], text=True, capture_output=True, check=True
        ).stdout
        help_flags = set(re.findall(r"--[a-z][a-z-]*", record_help))
        for flag in ("--source", "--context", "--option", "--choice", "--reason", "--prediction", "--prediction-confidence"):
            self.assertIn(f"`{flag}`", action)
            self.assertIn(flag, help_flags)
        self.assertIn("{user_choice,escalation_answer,correction}", record_help)
        self.assertIn("{none,low,medium,high}", record_help)
        confidence_values = "(`low`, `medium`, `high`, or `none`)"
        self.assertIn(confidence_values, action)
        self.assertIn("Keep every field generalized", action)
        alter_ego = (PLUGIN_ROOT / "agents" / "alter-ego.md").read_text(encoding="utf-8")
        self.assertIn("confidence (`low`, `medium`, or `high`)", alter_ego)
        alter_ego_codex = (PLUGIN_ROOT / "adapters" / "codex" / "alter-ego.toml").read_text(encoding="utf-8")
        self.assertIn("confidence (low, medium, or high)", alter_ego_codex)
        adapters = {
            "claude": (
                PLUGIN_ROOT / "adapters" / "claude" / "CLAUDE.snippet.md",
                "Record the user's decision in the same turn",
                "`human-distiller:human-distiller` skill with the `record` action",
                "`decision-distiller` subagent",
            ),
            "codex": (
                PLUGIN_ROOT / "adapters" / "codex" / "AGENTS.snippet.md",
                "Record in the same turn",
                "`$human-distiller` skill with the `record` action",
                "`decision_distiller` subagent",
            ),
        }
        for product, (path, recording_start, skill_invocation, distiller) in adapters.items():
            snippet = path.read_text(encoding="utf-8")
            recording = snippet[snippet.index(recording_start) :]
            with self.subTest(product=product):
                escalation = recording.index("ESCALATE_TO_USER")
                self.assertLess(escalation, recording.index("record --source escalation_answer"))
                correction = recording.index("record --source correction")
                self.assertLess(recording.index("`DECIDE`"), correction)
                self.assertLess(escalation, correction)
                for expected in (
                    "`--prediction`",
                    "`--prediction-confidence`",
                    "`--option`",
                    "`--choice`",
                    "`--reason`",
                    "Keep `--context` and every other field generalized",
                    confidence_values,
                    skill_invocation,
                    "run `stats`",
                    "distillation_review_due",
                    distiller,
                    "only after explicit approval",
                ):
                    self.assertIn(expected, recording)

    def test_manifests_share_name_and_version(self) -> None:
        codex = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        claude = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        marketplace = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual("human-distiller", codex["name"])
        self.assertEqual(codex["name"], claude["name"])
        self.assertEqual(codex["version"], claude["version"])
        self.assertEqual(codex["version"], marketplace["plugins"][0]["version"])
        self.assertEqual("./", marketplace["plugins"][0]["source"])

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
        rejected = run_cli(state_dir, "start", "--risk-ceiling", "low", expect=2)
        self.assertFalse(rejected["ok"])
        self.assertIn("must not be inside a Git repository", rejected["error"])
        self.assertFalse(state_dir.exists())


if __name__ == "__main__":
    unittest.main()
