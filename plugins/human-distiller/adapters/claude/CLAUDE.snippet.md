<!-- human-distiller:start -->
## Human Distiller: low-risk delegated decisions

When an already authorized task would otherwise pause for a minor preference question, inspect the repository context first. If the remaining choice is local, low-impact, testable, reversible, and outside the hard safety exclusions, use the `trusted-delegate` subagent before asking the user. Supply the goal, realistic options, evidence, constraints, and rollback.

Never delegate publication, external sending, merges, deployments, releases, destructive operations or data loss, production changes, authentication, credentials, permissions or security settings, purchases, handling personal data, legal/compliance decisions, or interpersonal judgments. Preference rules cannot relax these exclusions. Treat missing preferences as unknown and keep all personal state under `~/.human-distiller/`.
<!-- human-distiller:end -->
