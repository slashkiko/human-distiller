# Human Distiller

Human Distiller is a local-first plugin for Codex and Claude Code. It turns explicit answers into a preference profile, then lets a read-only subagent decide reversible implementation choices within the user's configured risk ceiling when work would otherwise pause for a preference question.

It never delegates publication, external sending, merges, deployments, releases, destructive operations or data loss, production changes, authentication, credentials, permissions or security settings, purchases, handling personal data, legal/compliance decisions, or interpersonal judgments. Preference rules cannot relax these exclusions.

## Status

This repository is an early private preview. The file formats and installation flow may change before a public release.

## Install from the private repository

Authenticate each tool with a GitHub account that can read `slashkiko/human-distiller`, then install the repository marketplace and plugin.

Codex:

```sh
codex plugin marketplace add slashkiko/human-distiller
codex plugin add human-distiller@human-distiller
```

Claude Code:

```sh
claude plugin marketplace add slashkiko/human-distiller
claude plugin install human-distiller@human-distiller
```

## Onboarding

Run the skill in your coding agent:

- Codex: `$human-distiller init`
- Claude Code: `/human-distiller:human-distiller init`

Claude Code autocomplete shows `[init|uninstall]`. Codex exposes both actions in the skill's default prompts. Enter only `$human-distiller` or `/human-distiller:human-distiller` to see the same concise help without starting either workflow.

The installed components are:

- `human-distiller`: the onboarding, profile, migration, and journal skill.
- `alter-ego` (`alter_ego` in Codex): the user's second self for decisions within the configured risk ceiling.
- `decision-distiller` (`decision_distiller` in Codex): the read-only agent that distills repeated decisions and drift into reviewable rule candidates.

First choose the highest risk level you want to delegate. This is a risk ceiling, not a question-depth setting. Every level asks eight questions: four common safety/operating questions and four questions tailored to the selected ceiling.

| Risk ceiling | Questions | Decisions that may be delegated |
| --- | ---: | --- |
| Low | 8 | Local, low-impact choices that are quick to test and undo (recommended initially) |
| Medium | 8 | Cross-module refactors, dependencies, and development data formats when rollback is clear |
| High | 8 | Architecture, public interfaces, and broad changes within the authorized task and with an explicit recovery plan |

The hard safety exclusions remain outside this scale and cannot be unlocked by choosing High. The skill states the exact question count before starting. You can stop at any time; answered questions are saved, unanswered questions remain unknown, and a later `init` resumes from the next unanswered question.

If a private-preview profile still uses the former `minimal`/`standard`/`broad` question-depth scheme, `init` reports that migration is required. It asks for a new Low/Medium/High risk ceiling, backs up the old answers, preserves the decision journal and distilled rules, and starts the new eight questions without inferring any answer.

Personal state is stored under `~/.human-distiller/` with restrictive file permissions. Mutating CLI commands reject a state directory inside any Git worktree, so personal state is never written into a repository.

## Update

`init` copies the adapter into `~/.claude/CLAUDE.md`, or into `~/.codex/AGENTS.md` and `~/.codex/agents/`, so updating the plugin does not change those copies. After updating the plugin with your product's plugin update command, start a new session (in Claude Code, `/reload-plugins` also works) so the new version is loaded. Then run `$human-distiller init` in Codex and `/human-distiller:human-distiller init` in Claude Code, once in each product you use. With a completed profile, `init` only reinstalls the adapter and asks no questions.

## Uninstall

Remove the product-specific adapters before removing the plugin bundle. This keeps uninstall reversible: the preference profile, journal, and backups under `~/.human-distiller/` are preserved by default.

Codex:

1. In a Codex chat, run `$human-distiller uninstall`.
2. In a terminal, remove the plugin:

   ```sh
   codex plugin remove human-distiller@human-distiller
   ```

Claude Code:

1. In a Claude Code session, run `/human-distiller:human-distiller uninstall`.
2. In a terminal, remove the plugin:

   ```sh
   claude plugin uninstall human-distiller@human-distiller
   ```

If no other installed plugin uses this repository marketplace, you may also remove the marketplace declaration:

```sh
codex plugin marketplace remove human-distiller
claude plugin marketplace remove human-distiller --scope user
```

Reinstalling later reuses the preserved profile. To retire the profile without immediately destroying it, move `~/.human-distiller/` to a backup location and inspect it before permanent deletion. Personal data removal is intentionally separate from plugin uninstall.

## Local development

```sh
mise install --locked
mise run test
mise run check
```

The repository follows the review-first supply-chain controls from `slashkiko/repository-baseline`: locked tools, a seven-day minimum release age, SHA-pinned GitHub Actions, workflow security checks, full-history secret scanning, CODEOWNERS, and non-automerged Renovate pull requests. `mise run repository-initialize` prints the GitHub settings it would apply; the `--configure-github` flag performs those external changes only after review.

To exercise onboarding without touching your real profile:

```sh
python3 plugins/human-distiller/skills/human-distiller/scripts/human_distiller.py --state-dir /tmp/human-distiller-demo start --risk-ceiling low
```

## Privacy model

- The repository contains only empty templates and synthetic test data.
- Answers, the generated profile, the decision journal, install records, and backups stay under the user's local state directory.
- Inputs that resemble common secrets are rejected instead of being persisted.
- Unanswered questions never become implied preferences.
- The hard safety boundary is: no publication, external sending, merges, deployments, releases, destructive operations or data loss, production changes, authentication, credentials, permissions or security settings, purchases, personal data handling, legal/compliance decisions, or interpersonal judgments. Preference rules cannot relax it.

See [SECURITY.md](SECURITY.md) before reporting a bug that involves a real profile or journal.
