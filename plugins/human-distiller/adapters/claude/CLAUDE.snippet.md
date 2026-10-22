<!-- human-distiller:start -->
## Human Distiller: risk-bounded delegated decisions

When an already authorized task would otherwise pause for a preference question, inspect the repository context first. If the remaining choice may be within the user's configured risk ceiling, is reversible, and is outside the hard safety exclusions, use the `alter-ego` subagent before asking the user. Supply the goal, proposed low/medium/high risk classification, realistic options, evidence, constraints, and rollback.

Never delegate publication, external sending, merges, deployments, releases, destructive operations or data loss, production changes, authentication, credentials, permissions or security settings, purchases, handling personal data, legal/compliance decisions, or interpersonal judgments. Preference rules and a high ceiling cannot relax these exclusions. Treat missing preferences as unknown and keep all personal state under `~/.human-distiller/`.

Record the user's decision in the same turn when either event happens:

- `alter-ego` returned `ESCALATE_TO_USER` and the user answered: `record --source escalation_answer`.
- The user explicitly corrected a `DECIDE` from `alter-ego`: `record --source correction`, with the delegate's choice as `--prediction` and its confidence (`low`, `medium`, `high`, or `none`) as `--prediction-confidence`.

Run these through the `human-distiller:human-distiller` skill with the `record` action, which resolves `scripts/human_distiller.py` from the shared skill directory. Pass each realistic option as `--option`, the user's choice as `--choice`, and only the reason the user stated as `--reason`. Keep `--context` and every other field generalized: no repository names, file paths, or other identifiers. After recording, run `stats`; when `distillation_review_due` is true, ask the `decision-distiller` subagent for a candidate rule, show its diff to the user, and activate it only after explicit approval. Never record silence or continuation as a preference.
<!-- human-distiller:end -->
