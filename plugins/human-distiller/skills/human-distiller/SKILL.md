---
name: human-distiller
description: Configure, resume, inspect, or migrate a local preference profile for delegating reversible coding decisions within a user-selected risk ceiling. Use when the user says human-distiller init/status/resume/migrate, asks to learn or distill their decision preferences, or wants a trusted delegate to handle choices without waiting for approval. Also use when recording explicit corrections or checking preference drift.
---

# Human Distiller

Build an explicit local preference profile, then use it only for reversible choices within the selected risk ceiling and fixed safety boundary. Never infer preferences from silence, skipped questions, or lack of correction.

## Locate the CLI

Resolve `scripts/human_distiller.py` relative to this `SKILL.md`. In Claude Code, `${CLAUDE_PLUGIN_ROOT}/skills/human-distiller/scripts/human_distiller.py` is also valid. Run it with Python 3 and parse its JSON output.

The supported installed state directory is `~/.human-distiller/`. Use `--state-dir` only for tests and demonstrations outside every Git worktree; v1 delegates do not read custom locations. Mutating CLI commands reject repository-contained state.

## `init` workflow

1. Run `status`.
2. If onboarding already has unanswered questions, tell the user the saved risk ceiling, total, answered count, and remaining count. Ask whether to resume; do not restart implicitly.
3. If onboarding has not started, ask the highest decision risk the user wants to delegate. Explain that all choices ask eight questions and change the question content, not the amount of profiling. This risk-ceiling question is not counted:
   - `low`: local, low-impact choices that are quick to test and undo. Recommend this initially when the user is unsure.
   - `medium`: cross-module refactors, dependencies, and development data-format choices when rollback is clear.
   - `high`: architecture, public interfaces, and broad changes within the authorized task and with an explicit recovery plan.
4. State that the hard safety exclusions remain outside the scale even at `high`. After the user chooses, say exactly: `このあと全8問です。質問内容は選んだ委譲リスク上限に合わせています。途中で「ここまで」と言えば、回答済みだけ保存して終了できます。`
5. Run `start --risk-ceiling <low|medium|high>` and ask the returned question verbatim. Ask one question at a time.
6. When the user answers, run `answer --question-id <id> --value <answer>`. Ask the returned next question. Do not rewrite the answer into a broader rule.
7. If the user says `ここまで`, `中断`, `stop`, or equivalent, run `pause`. Report the number answered and remaining, then stop questioning.
8. When all answers are complete, explain the local files that installation will touch, then run `install --product codex` or `install --product claude` for the current product. Report backups from the JSON result.

If secret-like input is rejected, do not repeat it. Ask the user to answer again without credentials, tokens, private keys, or personal data.

## Resume and status

- `status`: report the risk ceiling, total, answered, remaining, and whether installation has run. Do not print answer bodies unless the user explicitly asks.
- `resume`: run `resume`, state the remaining count, and ask the returned question.
- A completed onboarding remains completed. Starting again requires the user's explicit request and `start --restart`.

## Delegated decisions

Use the configured alter-ego subagent only after gathering repository context and task-specific evidence. Its identifier is `alter_ego` in Codex and `alter-ego` in Claude Code. Give it the user goal, the precise decision, a low/medium/high risk assessment, realistic alternatives, evidence, constraints, and rollback. The subagent must compare the assessed risk with the profile's configured ceiling.

Follow `DECIDE` unless it conflicts with current instructions or evidence. For `ESCALATE_TO_USER`, or when the safety boundary is uncertain, ask the user with the missing premise and trade-offs.

Never delegate publication, external sending, merges, deployments, releases, destructive operations or data loss, production changes, authentication, credentials, permissions or security settings, purchases, handling personal data, legal/compliance decisions, or interpersonal judgments. Preference rules cannot relax these exclusions.

## Recording and drift

Record only:

- an explicit reusable user choice;
- a user's answer after the delegate escalated;
- an explicit correction of a delegated decision.

Never record silence, continuation, secrets, personal data, repository identifiers, or external-message bodies. A direct user choice may update only a narrowly scoped active rule. Accumulate escalation answers and corrections; require three independent examples before proposing a candidate rule. Candidate rules require explicit user approval. Compare new evidence with active rules and report possible drift instead of silently overwriting them.

Use `record` for each qualifying event. For a direct `user_choice`, also use `set-rule --status active` in the same turn with the narrowest stated scope. For `escalation_answer` and `correction`, run `stats` after recording; when `distillation_review_due` is true, ask the decision-distiller subagent (`decision_distiller` in Codex, `decision-distiller` in Claude Code) whether at least three independent examples support a candidate. Show its candidate diff to the user. Run `set-rule --status active` only after explicit approval; otherwise leave the profile unchanged. Use `candidate` only when the user wants the proposal preserved locally before approval.

Use `references/journal-schema.md` for the record shape and `references/profile-template.md` for the safety boundary.

## Migration

For an existing local profile in another directory, run `migrate --source-dir <path>`. Migration copies only recognized state files into the local state directory, creates backups before replacing any existing files, and never writes personal data into the plugin repository.

If `status` returns `migration_required`, the profile uses the old question-depth scheme (`minimal`/`standard`/`broad`). Explain that those values cannot be safely converted to a decision risk ceiling. Ask the user to choose `low`, `medium`, or `high`, then run `migrate-v1 --risk-ceiling <choice>`. This backs up the old answers, preserves the journal and distilled rules, starts the new eight-question set with no inferred answers, and resumes at the first new question. Run `validate` afterward.

## Validation

Run `validate` after installation or migration. A valid setup has private file permissions, a consistent questionnaire state, no answer for an unanswered question, and matching installed adapter files.
