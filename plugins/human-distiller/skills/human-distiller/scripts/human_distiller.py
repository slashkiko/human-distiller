#!/usr/bin/env python3
"""Local state and adapter installer for Human Distiller."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple


SCHEMA_VERSION = 2
STATE_FILES = (
    "onboarding-state.json",
    "decision-profile.md",
    "decision-journal.jsonl",
    "install-state.json",
)
START_MARKER = "<!-- human-distiller:start -->"
END_MARKER = "<!-- human-distiller:end -->"
HARD_SAFETY_BOUNDARY = (
    "Never delegate publication, external sending, merges, deployments, releases, destructive operations or data loss, "
    "production changes, authentication, credentials, permissions or security settings, purchases, handling personal "
    "data, legal/compliance decisions, or interpersonal judgments. Preference rules cannot relax these exclusions."
)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*\S{12,}"),
)


class HumanDistillerError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def refuse_symlink(path: Path) -> None:
    # A symlinked state file would be replaced by a regular file, and a symlinked directory
    # on the way would receive the chmod and the write at its destination.
    if path.is_symlink() or Path(os.path.realpath(path.parent)) != path.parent:
        raise HumanDistillerError(f"Refusing to write private state through a symlink: {path}")


def ensure_private_dir(path: Path) -> None:
    refuse_symlink(path)
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def replace_file(path: Path, text: str, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_private_text(path: Path, text: str) -> None:
    refuse_symlink(path)
    ensure_private_dir(path.parent)
    replace_file(path, text, 0o600)


def resolve_user_target(path: Path) -> Path:
    """Return the file a user config path points to, after checking that it and its directory are writable."""
    # On a symlink loop, Python 3.11 raises RuntimeError; newer versions return a path that still contains a symlink.
    try:
        real = path.resolve()
    except (OSError, RuntimeError) as exc:
        raise HumanDistillerError(f"Cannot resolve {path}: symlink loop or unreadable link") from exc
    if any(part.is_symlink() for part in (real, *real.parents)):
        raise HumanDistillerError(f"Cannot resolve {path}: symlink loop or unreadable link")
    existing = real.parent
    while not existing.exists():
        existing = existing.parent
    if not existing.is_dir() or not os.access(existing, os.W_OK | os.X_OK):
        raise HumanDistillerError(f"Cannot write {path}: {existing} is not a writable directory")
    # Also false for a file flagged uchg, which os.replace and unlink would fail on.
    if real.exists() and not os.access(real, os.W_OK):
        raise HumanDistillerError(f"Cannot write {path}: {real} is read-only")
    return real


def write_user_text(path: Path, text: str) -> None:
    """Write a user config file through any symlink, keeping its existing mode."""
    real = resolve_user_target(path)
    missing = []
    parent = real.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    mode = stat.S_IMODE(real.stat().st_mode) if real.exists() else 0o600
    replace_file(real, text, mode)


def write_private_json(path: Path, value: Any) -> None:
    write_private_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HumanDistillerError(f"Cannot read valid JSON from {path}: {exc}") from exc


def state_dir_from_args(args: argparse.Namespace) -> Path:
    raw = args.state_dir or os.environ.get("HUMAN_DISTILLER_HOME")
    return Path(raw).expanduser().resolve() if raw else (Path.home() / ".human-distiller").resolve()


def ensure_safe_state_location(directory: Path) -> None:
    resolved = directory.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists():
            raise HumanDistillerError(
                "State directory must not be inside a Git repository. "
                "Use ~/.human-distiller or a temporary directory outside the worktree."
            )


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_questionnaire() -> dict[str, Any]:
    data = read_json(skill_root() / "references" / "questions.json")
    question_ids = [item["id"] for item in data.get("questions", [])]
    if len(question_ids) != len(set(question_ids)):
        raise HumanDistillerError("Question IDs must be unique")
    for level, ids in data.get("risk_levels", {}).items():
        missing = set(ids) - set(question_ids)
        if missing:
            raise HumanDistillerError(f"Risk level {level} references unknown questions: {sorted(missing)}")
    return data


def questions_by_id(questionnaire: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {item["id"]: item for item in questionnaire["questions"]}


def load_state(directory: Path, required: bool = True) -> dict[str, Any] | None:
    path = directory / "onboarding-state.json"
    if not path.exists():
        if required:
            raise HumanDistillerError("Onboarding has not started. Run start --risk-ceiling first.")
        return None
    return read_json(path)


def is_legacy_state(state: dict[str, Any]) -> bool:
    return state.get("schema_version") == 1 and "level" in state and "risk_ceiling" not in state


def require_current_state(state: dict[str, Any]) -> None:
    if is_legacy_state(state):
        raise HumanDistillerError(
            "Legacy question-depth onboarding requires migration. "
            "Choose a risk ceiling and run migrate-v1 --risk-ceiling low|medium|high."
        )
    if state.get("schema_version") != SCHEMA_VERSION:
        raise HumanDistillerError(f"Unsupported onboarding schema version: {state.get('schema_version')}")


def legacy_state_summary(state: dict[str, Any]) -> dict[str, Any]:
    question_ids = state.get("question_ids", [])
    answers = state.get("answers", {})
    answered = sum(1 for question_id in question_ids if question_id in answers)
    return {
        "configured": True,
        "status": "migration_required",
        "migration_required": True,
        "legacy_level": state.get("level"),
        "risk_ceiling": None,
        "total": len(question_ids),
        "answered": answered,
        "remaining": len(question_ids) - answered,
        "next_question": None,
    }


def next_question(state: dict[str, Any], questionnaire: dict[str, Any]) -> dict[str, str] | None:
    answers = state.get("answers", {})
    index = questions_by_id(questionnaire)
    for question_id in state["question_ids"]:
        if question_id not in answers:
            return index[question_id]
    return None


def state_summary(state: dict[str, Any], questionnaire: dict[str, Any]) -> dict[str, Any]:
    require_current_state(state)
    total = len(state["question_ids"])
    answered = sum(1 for question_id in state["question_ids"] if question_id in state.get("answers", {}))
    question = next_question(state, questionnaire)
    return {
        "configured": True,
        "risk_ceiling": state["risk_ceiling"],
        "status": state["status"],
        "total": total,
        "answered": answered,
        "remaining": total - answered,
        "next_question": question,
    }


def contains_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in SECRET_PATTERNS)


def escape_table(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def render_profile(state: dict[str, Any], questionnaire: dict[str, Any]) -> str:
    index = questions_by_id(questionnaire)
    rows = []
    for question_id in state["question_ids"]:
        if question_id in state.get("answers", {}):
            prompt = escape_table(index[question_id]["prompt"])
            answer = escape_table(state["answers"][question_id])
            rows.append(f"| `{question_id}` | {prompt} | {answer} |")
    table = "\n".join(rows) if rows else "| _No explicit answers yet_ |  |  |"
    rule_rows = []
    for rule in state.get("rules", []):
        rule_rows.append(
            "| `{id}` | {status} | {scope} | {preference} | {evidence} |".format(
                id=escape_table(rule["id"]),
                status=escape_table(rule["status"]),
                scope=escape_table(rule["scope"]),
                preference=escape_table(rule["preference"]),
                evidence=escape_table(", ".join(rule.get("evidence", []))),
            )
        )
    rules_table = "\n".join(rule_rows) if rule_rows else "| _No distilled rules yet_ |  |  |  |  |"
    return f"""# Decision profile

Generated locally by Human Distiller. Missing questions are unknown and must not be inferred.

- Schema version: {SCHEMA_VERSION}
- Question set: {state['question_set_version']}
- Delegation risk ceiling: {state['risk_ceiling']}
- Updated: {state['updated_at']}

## Hard safety boundary

{HARD_SAFETY_BOUNDARY}

## Explicit answers

| ID | Question | Answer |
| --- | --- | --- |
{table}

## Distilled rules

Only `active` rules may guide delegated decisions. `candidate` rules await explicit approval, and `superseded` rules are retained only as history.

| ID | Status | Scope | Preference | Evidence |
| --- | --- | --- | --- | --- |
{rules_table}
"""


def copy_private(source: Path, target: Path) -> None:
    # Content only: copy2 would also carry the mode and file flags such as uchg, and the chmod would then fail.
    shutil.copyfile(source, target)
    os.chmod(target, 0o600)


def backup_files(directory: Path, names: tuple[str, ...] = STATE_FILES) -> list[str]:
    existing = [directory / name for name in names if (directory / name).exists()]
    if not existing:
        return []
    backup_dir = directory / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ensure_private_dir(backup_dir)
    copied = []
    for source in existing:
        target = backup_dir / source.name
        copy_private(source, target)
        copied.append(str(target))
    return copied


def command_start(args: argparse.Namespace, directory: Path) -> dict[str, Any]:
    questionnaire = load_questionnaire()
    if args.risk_ceiling not in questionnaire["risk_levels"]:
        raise HumanDistillerError(f"Unknown risk ceiling: {args.risk_ceiling}")
    existing = load_state(directory, required=False)
    backups: list[str] = []
    if existing and not args.restart:
        require_current_state(existing)
        if existing.get("risk_ceiling") != args.risk_ceiling:
            raise HumanDistillerError("Onboarding already exists at another risk ceiling. Resume it or use --restart explicitly.")
        result = state_summary(existing, questionnaire)
        result["resumed_existing"] = True
        result["backups"] = []
        return result
    ensure_private_dir(directory)
    if existing and args.restart:
        require_current_state(existing)
        backups = backup_files(directory)
    timestamp = now_iso()
    state = {
        "schema_version": SCHEMA_VERSION,
        "question_set_version": questionnaire["question_set_version"],
        "risk_ceiling": args.risk_ceiling,
        "status": "in_progress",
        "question_ids": questionnaire["risk_levels"][args.risk_ceiling],
        "answers": {},
        "rules": [],
        "started_at": timestamp,
        "updated_at": timestamp,
    }
    write_private_json(directory / "onboarding-state.json", state)
    write_private_text(directory / "decision-profile.md", render_profile(state, questionnaire))
    journal = directory / "decision-journal.jsonl"
    if not journal.exists() or args.restart:
        write_private_text(journal, "")
    result = state_summary(state, questionnaire)
    result["resumed_existing"] = False
    result["backups"] = backups
    return result


def command_answer(args: argparse.Namespace, directory: Path) -> dict[str, Any]:
    questionnaire = load_questionnaire()
    state = load_state(directory)
    assert state is not None
    require_current_state(state)
    expected = next_question(state, questionnaire)
    if expected is None:
        raise HumanDistillerError("All onboarding questions are already answered.")
    if args.question_id != expected["id"]:
        raise HumanDistillerError(f"Expected question {expected['id']}, not {args.question_id}.")
    value = args.value.strip()
    if not value:
        raise HumanDistillerError("An empty answer is not a preference. Pause instead.")
    if contains_secret(value):
        raise HumanDistillerError("The answer resembles a secret and was not saved. Remove credentials or tokens and try again.")
    state.setdefault("answers", {})[args.question_id] = value
    state["updated_at"] = now_iso()
    if next_question(state, questionnaire) is None:
        state["status"] = "complete"
        state["completed_at"] = state["updated_at"]
    else:
        state["status"] = "in_progress"
    write_private_json(directory / "onboarding-state.json", state)
    write_private_text(directory / "decision-profile.md", render_profile(state, questionnaire))
    return state_summary(state, questionnaire)


def command_pause(directory: Path) -> dict[str, Any]:
    questionnaire = load_questionnaire()
    state = load_state(directory)
    assert state is not None
    require_current_state(state)
    if next_question(state, questionnaire) is not None:
        state["status"] = "paused"
        state["updated_at"] = now_iso()
        write_private_json(directory / "onboarding-state.json", state)
    return state_summary(state, questionnaire)


def command_resume(directory: Path) -> dict[str, Any]:
    questionnaire = load_questionnaire()
    state = load_state(directory)
    assert state is not None
    require_current_state(state)
    if next_question(state, questionnaire) is not None:
        state["status"] = "in_progress"
        state["updated_at"] = now_iso()
        write_private_json(directory / "onboarding-state.json", state)
    return state_summary(state, questionnaire)


def command_status(directory: Path) -> dict[str, Any]:
    questionnaire = load_questionnaire()
    state = load_state(directory, required=False)
    if state is None:
        return {"configured": False, "status": "not_started", "installed_products": []}
    if is_legacy_state(state):
        result = legacy_state_summary(state)
        result["installed_products"] = []
        return result
    result = state_summary(state, questionnaire)
    install_path = directory / "install-state.json"
    install_state = read_json(install_path) if install_path.exists() else {"products": {}}
    result["installed_products"] = sorted(install_state.get("products", {}).keys())
    return result


def backup_target(target: Path, directory: Path, product: str, label: str) -> str:
    backup_dir = directory / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") / product
    ensure_private_dir(backup_dir)
    backup = backup_dir / label
    copy_private(target, backup)
    return str(backup)


class Change(NamedTuple):
    target: Path
    text: str | None  # None removes the file


def read_user_text(target: Path) -> str:
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise HumanDistillerError(f"Cannot read {target} as UTF-8") from exc


def plan_owned_file(source: Path, target: Path) -> tuple[Change | None, str | None]:
    content = source.read_bytes()
    if target.exists() and target.read_bytes() == content:
        return None, None
    # Uninstall cannot undo a write through a symlink, so install does not make one.
    if target.is_symlink():
        return None, str(target)
    return Change(target, content.decode("utf-8")), None


def plan_legacy_removal(target: Path, expected_name: str) -> tuple[Change | None, str | None]:
    if not target.exists():
        return None, None
    content = target.read_bytes()
    if f'name = "{expected_name}"'.encode() not in content or b".human-distiller" not in content or target.is_symlink():
        return None, str(target)
    return Change(target, None), None


def plan_marked_section(target: Path, snippet: str) -> Change | None:
    if START_MARKER not in snippet or END_MARKER not in snippet:
        raise HumanDistillerError("Adapter snippet is missing markers")
    old = read_user_text(target) if target.exists() else ""
    if START_MARKER in old or END_MARKER in old:
        if old.count(START_MARKER) != 1 or old.count(END_MARKER) != 1:
            raise HumanDistillerError(f"Cannot safely update malformed Human Distiller markers in {target}")
        start = old.index(START_MARKER)
        end = old.index(END_MARKER, start) + len(END_MARKER)
        new = old[:start] + snippet.strip() + old[end:]
    else:
        separator = "" if not old else ("\n" if old.endswith("\n") else "\n\n")
        new = old + separator + snippet.strip() + "\n"
    return None if new == old else Change(target, new)


def plan_owned_removal(source: Path, target: Path) -> tuple[Change | None, str | None]:
    # Unlinking a symlink would leave the adapter in the file it points to, if any.
    if target.is_symlink():
        return None, str(target)
    if not target.exists():
        return None, None
    if target.read_bytes() != source.read_bytes():
        return None, str(target)
    return Change(target, None), None


def plan_section_removal(target: Path, snippet: str) -> tuple[Change | None, str | None]:
    if START_MARKER not in snippet or END_MARKER not in snippet:
        raise HumanDistillerError("Adapter snippet is missing markers")
    if not target.exists():
        return None, None
    old = read_user_text(target)
    if START_MARKER not in old and END_MARKER not in old:
        return None, None
    if old.count(START_MARKER) != 1 or old.count(END_MARKER) != 1:
        return None, str(target)
    start = old.index(START_MARKER)
    if old.index(END_MARKER) < start:
        return None, str(target)
    end = old.index(END_MARKER, start) + len(END_MARKER)
    if old[start:end] != snippet.strip():
        return None, str(target)
    before = old[:start].rstrip("\n")
    after = old[end:].lstrip("\n")
    if before and after:
        new = before + "\n\n" + after
    elif before:
        new = before + "\n"
    else:
        new = after
    return Change(target, new), None


def apply_changes(changes: list[Change], directory: Path, product: str) -> list[str]:
    """Check every target, back up every existing one, then write; nothing is written if a check fails."""
    for change in changes:
        resolve_user_target(change.target)
    backups = [
        backup_target(change.target, directory, product, change.target.name)
        for change in changes
        if change.target.exists()
    ]
    for change in changes:
        if change.text is None:
            change.target.unlink()
        else:
            write_user_text(change.target, change.text)
    return backups


def command_install(args: argparse.Namespace, directory: Path) -> dict[str, Any]:
    state = load_state(directory)
    assert state is not None
    require_current_state(state)
    if state.get("status") != "complete" and not args.allow_incomplete:
        raise HumanDistillerError("Onboarding is incomplete. Complete it or explicitly use --allow-incomplete.")
    ensure_private_dir(directory)
    changes: list[Change] = []
    conflicts: list[str] = []
    legacy_conflicts: list[str] = []
    root = plugin_root()
    if args.product == "codex":
        config_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
        agents_dir = config_home / "agents"
        for legacy_name, expected_name in (
            ("trusted-delegate.toml", "trusted_delegate"),
            ("preference-distiller.toml", "preference_distiller"),
        ):
            change, conflict = plan_legacy_removal(agents_dir / legacy_name, expected_name)
            changes += [change] if change else []
            legacy_conflicts += [conflict] if conflict else []
        for name in ("alter-ego.toml", "decision-distiller.toml"):
            change, conflict = plan_owned_file(root / "adapters" / "codex" / name, agents_dir / name)
            changes += [change] if change else []
            conflicts += [conflict] if conflict else []
        snippet = (root / "adapters" / "codex" / "AGENTS.snippet.md").read_text(encoding="utf-8")
        target = config_home / "AGENTS.md"
        targets = [str(agents_dir / "alter-ego.toml"), str(agents_dir / "decision-distiller.toml"), str(target)]
    else:
        config_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")).expanduser()
        snippet = (root / "adapters" / "claude" / "CLAUDE.snippet.md").read_text(encoding="utf-8")
        target = config_home / "CLAUDE.md"
        targets = [str(target), str(root / "agents" / "alter-ego.md"), str(root / "agents" / "decision-distiller.md")]
    change = plan_marked_section(target, snippet)
    changes += [change] if change else []
    backups = apply_changes(changes, directory, args.product)

    install_path = directory / "install-state.json"
    install_state = read_json(install_path) if install_path.exists() else {"schema_version": SCHEMA_VERSION, "products": {}}
    install_state.setdefault("products", {})[args.product] = {
        "installed_at": now_iso(),
        "targets": targets,
    }
    write_private_json(install_path, install_state)
    return {
        "product": args.product,
        "changed": [str(change.target) for change in changes if change.text is not None],
        "removed": [str(change.target) for change in changes if change.text is None],
        "conflicts": conflicts,
        "legacy_conflicts": legacy_conflicts,
        "backups": backups,
        "idempotent": not changes,
    }


def command_uninstall(args: argparse.Namespace, directory: Path) -> dict[str, Any]:
    changes: list[Change] = []
    conflicts: list[str] = []
    root = plugin_root()
    if args.product == "codex":
        config_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
        for name in ("alter-ego.toml", "decision-distiller.toml"):
            change, conflict = plan_owned_removal(root / "adapters" / "codex" / name, config_home / "agents" / name)
            changes += [change] if change else []
            conflicts += [conflict] if conflict else []
        target = config_home / "AGENTS.md"
        snippet = (root / "adapters" / "codex" / "AGENTS.snippet.md").read_text(encoding="utf-8")
    else:
        config_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")).expanduser()
        target = config_home / "CLAUDE.md"
        snippet = (root / "adapters" / "claude" / "CLAUDE.snippet.md").read_text(encoding="utf-8")
    change, conflict = plan_section_removal(target, snippet)
    changes += [change] if change else []
    conflicts += [conflict] if conflict else []
    backups = apply_changes(changes, directory, args.product)

    install_path = directory / "install-state.json"
    if install_path.exists():
        install_state = read_json(install_path)
        products = install_state.setdefault("products", {})
        if args.product in products:
            del products[args.product]
            write_private_json(install_path, install_state)

    return {
        "product": args.product,
        "removed": [str(change.target) for change in changes],
        "conflicts": conflicts,
        "backups": backups,
        "personal_state_preserved": str(directory),
        "idempotent": not changes and not conflicts,
    }


def command_migrate(args: argparse.Namespace, directory: Path) -> dict[str, Any]:
    source_dir = Path(args.source_dir).expanduser().resolve()
    if source_dir == directory.resolve():
        raise HumanDistillerError("Source and destination state directories are identical.")
    if not source_dir.is_dir():
        raise HumanDistillerError(f"Migration source is not a directory: {source_dir}")
    sources = [source_dir / name for name in STATE_FILES if (source_dir / name).is_file()]
    if not sources:
        raise HumanDistillerError("Migration source contains no recognized state files.")
    with tempfile.TemporaryDirectory(prefix="human-distiller-migrate-") as temporary:
        staging = Path(temporary).resolve()
        for source in sources:
            write_private_text(staging / source.name, source.read_text(encoding="utf-8"))
        staged_validation = validate_state(staging, check_adapters=False)
        if not staged_validation["valid"]:
            raise HumanDistillerError(f"Migration source is invalid: {staged_validation['errors']}")
    ensure_private_dir(directory)
    backups = backup_files(directory, tuple(path.name for path in sources))
    migrated = []
    for source in sources:
        target = directory / source.name
        write_private_text(target, source.read_text(encoding="utf-8"))
        migrated.append(str(target))
    return {"migrated": migrated, "backups": backups, "validation": validate_state(directory)}


def command_migrate_v1(args: argparse.Namespace, directory: Path) -> dict[str, Any]:
    questionnaire = load_questionnaire()
    legacy = load_state(directory)
    assert legacy is not None
    if not is_legacy_state(legacy):
        raise HumanDistillerError("The local onboarding state is not a supported v1 question-depth state.")
    backups = backup_files(directory)
    timestamp = now_iso()
    state = {
        "schema_version": SCHEMA_VERSION,
        "question_set_version": questionnaire["question_set_version"],
        "risk_ceiling": args.risk_ceiling,
        "status": "in_progress",
        "question_ids": questionnaire["risk_levels"][args.risk_ceiling],
        "answers": {},
        "rules": legacy.get("rules", []),
        "started_at": timestamp,
        "updated_at": timestamp,
        "migrated_from": {
            "schema_version": 1,
            "legacy_level": legacy.get("level"),
            "legacy_answer_count": len(legacy.get("answers", {})),
        },
    }
    write_private_json(directory / "onboarding-state.json", state)
    write_private_text(directory / "decision-profile.md", render_profile(state, questionnaire))
    journal = directory / "decision-journal.jsonl"
    if not journal.exists():
        write_private_text(journal, "")
    result = state_summary(state, questionnaire)
    result.update(
        {
            "migration_required": False,
            "backups": backups,
            "legacy_answers_preserved_in_backup": len(legacy.get("answers", {})),
            "rules_preserved": len(state["rules"]),
        }
    )
    return result


def command_record(args: argparse.Namespace, directory: Path) -> dict[str, Any]:
    load_state(directory)
    fields = [args.context, *args.options, args.choice, args.reason, args.outcome, args.prediction]
    if any(contains_secret(value) for value in fields if value):
        raise HumanDistillerError("The record resembles a secret and was not saved.")
    record = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": now_iso(),
        "source": args.source,
        "context": args.context,
        "options": args.options,
        "choice": args.choice,
        "reason": args.reason,
        "outcome": args.outcome,
        "prediction": args.prediction,
        "prediction_confidence": args.prediction_confidence,
    }
    journal = directory / "decision-journal.jsonl"
    old = journal.read_text(encoding="utf-8") if journal.exists() else ""
    write_private_text(journal, old + json.dumps(record, ensure_ascii=False) + "\n")
    return {"recorded": True, "source": args.source, "journal": str(journal)}


def command_stats(directory: Path) -> dict[str, Any]:
    journal = directory / "decision-journal.jsonl"
    counts = {"user_choice": 0, "escalation_answer": 0, "correction": 0}
    if journal.exists():
        for line_number, line in enumerate(journal.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                source = json.loads(line).get("source")
            except json.JSONDecodeError as exc:
                raise HumanDistillerError(f"Invalid journal JSON on line {line_number}: {exc}") from exc
            if source not in counts:
                raise HumanDistillerError(f"Unknown journal source on line {line_number}: {source}")
            counts[source] += 1
    review_count = counts["escalation_answer"] + counts["correction"]
    return {
        "counts": counts,
        "review_count": review_count,
        "distillation_review_due": review_count > 0 and review_count % 3 == 0,
    }


def command_set_rule(args: argparse.Namespace, directory: Path) -> dict[str, Any]:
    questionnaire = load_questionnaire()
    state = load_state(directory)
    assert state is not None
    require_current_state(state)
    values = [args.rule_id, args.scope, args.preference, args.reason, *args.evidence]
    if any(contains_secret(value) for value in values if value):
        raise HumanDistillerError("The rule resembles a secret and was not saved.")
    rule = {
        "id": args.rule_id,
        "status": args.status,
        "scope": args.scope,
        "preference": args.preference,
        "reason": args.reason,
        "evidence": args.evidence,
        "last_confirmed": now_iso(),
    }
    rules = state.setdefault("rules", [])
    existing_index = next((index for index, item in enumerate(rules) if item.get("id") == args.rule_id), None)
    if existing_index is None:
        rules.append(rule)
        action = "created"
    else:
        rules[existing_index] = rule
        action = "updated"
    state["updated_at"] = now_iso()
    write_private_json(directory / "onboarding-state.json", state)
    write_private_text(directory / "decision-profile.md", render_profile(state, questionnaire))
    return {"rule": rule, "action": action, "profile": str(directory / "decision-profile.md")}


def validate_state(directory: Path, check_adapters: bool = True) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    questionnaire = load_questionnaire()
    state = load_state(directory, required=False)
    if state is None:
        errors.append("Onboarding state is missing")
    elif is_legacy_state(state):
        if not isinstance(state.get("question_ids"), list) or not isinstance(state.get("answers"), dict):
            errors.append("Legacy onboarding state is malformed")
        warnings.append("Legacy question-depth onboarding requires an explicit risk-ceiling migration")
    else:
        if state.get("schema_version") != SCHEMA_VERSION:
            errors.append("Unsupported onboarding schema version")
        risk_ceiling = state.get("risk_ceiling")
        expected_ids = questionnaire.get("risk_levels", {}).get(risk_ceiling)
        if expected_ids is None:
            errors.append("Unknown delegation risk ceiling")
        elif state.get("question_ids") != expected_ids:
            errors.append("Question IDs do not match the selected risk ceiling")
        unknown_answers = set(state.get("answers", {})) - set(state.get("question_ids", []))
        if unknown_answers:
            errors.append(f"Answers contain unknown question IDs: {sorted(unknown_answers)}")
        complete = next_question(state, questionnaire) is None if expected_ids is not None else False
        if complete and state.get("status") != "complete":
            errors.append("All answers exist but status is not complete")
        if not complete and state.get("status") == "complete":
            errors.append("Status is complete but unanswered questions remain")
        rule_ids = [rule.get("id") for rule in state.get("rules", [])]
        if len(rule_ids) != len(set(rule_ids)):
            errors.append("Distilled rule IDs must be unique")
        for rule in state.get("rules", []):
            if rule.get("status") not in {"active", "candidate", "superseded"}:
                errors.append(f"Unknown rule status: {rule.get('status')}")
    if directory.exists() and stat.S_IMODE(directory.stat().st_mode) & 0o077:
        errors.append("State directory permissions are broader than 0700")
    for name in STATE_FILES:
        path = directory / name
        if path.exists() and stat.S_IMODE(path.stat().st_mode) & 0o077:
            errors.append(f"File permissions are broader than 0600: {name}")
    if not (directory / "decision-journal.jsonl").exists():
        warnings.append("Decision journal does not exist yet")
    install_path = directory / "install-state.json"
    if install_path.exists() and check_adapters:
        try:
            install_state = read_json(install_path)
            root = plugin_root()
            for product, product_state in install_state.get("products", {}).items():
                targets = [Path(item).expanduser() for item in product_state.get("targets", [])]
                if product == "codex":
                    if len(targets) < 3:
                        errors.append("Codex install state is missing adapter targets")
                        continue
                    for source_name, target in zip(("alter-ego.toml", "decision-distiller.toml"), targets[:2]):
                        source = root / "adapters" / "codex" / source_name
                        if not target.exists() or target.read_bytes() != source.read_bytes():
                            errors.append(f"Installed Codex adapter does not match: {target}")
                    snippet = (root / "adapters" / "codex" / "AGENTS.snippet.md").read_text(encoding="utf-8").strip()
                    if not marked_section_matches(targets[2], snippet):
                        errors.append(f"Installed Codex instructions do not match: {targets[2]}")
                elif product == "claude":
                    if not targets:
                        errors.append("Claude install state is missing its CLAUDE.md target")
                        continue
                    snippet = (root / "adapters" / "claude" / "CLAUDE.snippet.md").read_text(encoding="utf-8").strip()
                    if not marked_section_matches(targets[0], snippet):
                        errors.append(f"Installed Claude instructions do not match: {targets[0]}")
                else:
                    errors.append(f"Unknown installed product: {product}")
        except (HumanDistillerError, OSError) as exc:
            errors.append(f"Cannot validate installed adapters: {exc}")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "migration_required": bool(state and is_legacy_state(state)),
    }


def marked_section_matches(target: Path, expected: str) -> bool:
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    if content.count(START_MARKER) != 1 or content.count(END_MARKER) != 1:
        return False
    start = content.index(START_MARKER)
    end = content.index(END_MARKER, start) + len(END_MARKER)
    return content[start:end].strip() == expected


def command_validate(directory: Path) -> dict[str, Any]:
    return validate_state(directory)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", help="Override the local state directory for tests and demonstrations")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Start or inspect onboarding")
    start.add_argument("--risk-ceiling", required=True, choices=("low", "medium", "high"))
    start.add_argument("--restart", action="store_true", help="Back up and explicitly restart onboarding")

    answer = subparsers.add_parser("answer", help="Save the next explicit answer")
    answer.add_argument("--question-id", required=True)
    answer.add_argument("--value", required=True)

    subparsers.add_parser("pause", help="Pause onboarding without filling unanswered questions")
    subparsers.add_parser("resume", help="Resume at the next unanswered question")
    subparsers.add_parser("status", help="Show progress without answer bodies")

    install = subparsers.add_parser("install", help="Install product-specific local adapters")
    install.add_argument("--product", required=True, choices=("codex", "claude"))
    install.add_argument("--allow-incomplete", action="store_true", help="Install while unanswered preferences remain unknown")

    uninstall = subparsers.add_parser("uninstall", help="Remove product-specific adapters while preserving personal state")
    uninstall.add_argument("--product", required=True, choices=("codex", "claude"))

    migrate = subparsers.add_parser("migrate", help="Copy recognized state files from another local directory")
    migrate.add_argument("--source-dir", required=True)

    migrate_v1 = subparsers.add_parser("migrate-v1", help="Convert v1 question-depth state after choosing a risk ceiling")
    migrate_v1.add_argument("--risk-ceiling", required=True, choices=("low", "medium", "high"))

    record = subparsers.add_parser("record", help="Append an explicit reusable decision to the local journal")
    record.add_argument("--source", required=True, choices=("user_choice", "escalation_answer", "correction"))
    record.add_argument("--context", required=True)
    record.add_argument("--option", dest="options", action="append", required=True)
    record.add_argument("--choice", required=True)
    record.add_argument("--reason", default="")
    record.add_argument("--outcome", default="")
    record.add_argument("--prediction", default="")
    record.add_argument("--prediction-confidence", default="none", choices=("none", "low", "medium", "high"))

    subparsers.add_parser("stats", help="Count journal evidence and report whether a review is due")

    set_rule = subparsers.add_parser("set-rule", help="Create or update a distilled rule after the required approval")
    set_rule.add_argument("--rule-id", required=True)
    set_rule.add_argument("--status", required=True, choices=("active", "candidate", "superseded"))
    set_rule.add_argument("--scope", required=True)
    set_rule.add_argument("--preference", required=True)
    set_rule.add_argument("--reason", default="")
    set_rule.add_argument("--evidence", action="append", default=[])

    subparsers.add_parser("validate", help="Validate questionnaire state and file permissions")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    directory = state_dir_from_args(args)
    if args.command not in {"status", "validate", "stats"}:
        ensure_safe_state_location(directory)
        # Refuse before the first write, so a linked state file does not stop the command halfway.
        for name in (*STATE_FILES, "backups"):
            refuse_symlink(directory / name)
    if args.command == "start":
        return command_start(args, directory)
    if args.command == "answer":
        return command_answer(args, directory)
    if args.command == "pause":
        return command_pause(directory)
    if args.command == "resume":
        return command_resume(directory)
    if args.command == "status":
        return command_status(directory)
    if args.command == "install":
        return command_install(args, directory)
    if args.command == "uninstall":
        return command_uninstall(args, directory)
    if args.command == "migrate":
        return command_migrate(args, directory)
    if args.command == "migrate-v1":
        return command_migrate_v1(args, directory)
    if args.command == "record":
        return command_record(args, directory)
    if args.command == "stats":
        return command_stats(directory)
    if args.command == "set-rule":
        return command_set_rule(args, directory)
    if args.command == "validate":
        return command_validate(directory)
    raise AssertionError(args.command)


def main() -> int:
    try:
        result = run(build_parser().parse_args())
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
        return 0
    except (HumanDistillerError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
