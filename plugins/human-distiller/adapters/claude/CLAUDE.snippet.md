<!-- human-distiller:start -->
## Human Distiller: risk-bounded delegated decisions

When an already authorized task would otherwise pause for a preference question, inspect the repository context first. If the remaining choice may be within the user's configured risk ceiling, is reversible, and is outside the hard safety exclusions, use the `alter-ego` subagent before asking the user. Supply the goal, proposed low/medium/high risk classification, realistic options, evidence, constraints, and rollback.

Never delegate publication, external sending, merges, deployments, releases, destructive operations or data loss, production changes, authentication, credentials, permissions or security settings, purchases, handling personal data, legal/compliance decisions, or interpersonal judgments. Preference rules and a high ceiling cannot relax these exclusions. Treat missing preferences as unknown and keep all personal state under `~/.human-distiller/`.
<!-- human-distiller:end -->
