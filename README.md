# Human Distiller

Human Distiller is a local-first plugin for Codex and Claude Code. It turns explicit answers into a preference profile, then lets a read-only subagent decide reversible, low-risk implementation choices when work would otherwise pause for a minor preference question.

It never delegates publication, external sending, merges, deployments, releases, destructive operations or data loss, production changes, authentication, credentials, permissions or security settings, purchases, handling personal data, legal/compliance decisions, or interpersonal judgments. Preference rules cannot relax these exclusions.

## Status

This repository is an early private preview. The file formats and installation flow may change before a public release.

## Onboarding

Run the skill in your coding agent:

- Codex: `$human-distiller init`
- Claude Code: `/human-distiller:human-distiller init`

First choose how broadly you want to delegate:

| Level | Questions | Intended scope |
| --- | ---: | --- |
| Minimal | 4 | Safety boundaries and basic operating preferences |
| Standard | 8 | Common implementation trade-offs (recommended) |
| Broad | 12 | Additional quality, UI, and unfamiliar-situation preferences |

The skill states the exact question count before starting. You can stop at any time; answered questions are saved, unanswered questions remain unknown, and a later `init` resumes from the next unanswered question.

Personal state is stored under `~/.human-distiller/` with restrictive file permissions. Mutating CLI commands reject a state directory inside any Git worktree, so personal state is never written into a repository.

## Local development

```sh
python3 -m unittest discover -s tests -v
python3 plugins/human-distiller/skills/human-distiller/scripts/human_distiller.py validate
```

To exercise onboarding without touching your real profile:

```sh
python3 plugins/human-distiller/skills/human-distiller/scripts/human_distiller.py --state-dir /tmp/human-distiller-demo start --level minimal
```

## Privacy model

- The repository contains only empty templates and synthetic test data.
- Answers, the generated profile, the decision journal, install records, and backups stay under the user's local state directory.
- Inputs that resemble common secrets are rejected instead of being persisted.
- Unanswered questions never become implied preferences.
- The hard safety boundary is: no publication, external sending, merges, deployments, releases, destructive operations or data loss, production changes, authentication, credentials, permissions or security settings, purchases, personal data handling, legal/compliance decisions, or interpersonal judgments. Preference rules cannot relax it.

See [SECURITY.md](SECURITY.md) before reporting a bug that involves a real profile or journal.
