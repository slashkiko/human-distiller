<!-- human-distiller:start -->
## Human Distiller: low-risk delegated decisions

When an already authorized task would otherwise pause for a minor preference question, first inspect the repository context and task-specific evidence. If the remaining choice is local, low-impact, testable, reversible, and outside the hard safety exclusions, ask the `trusted_delegate` subagent before asking the user. Provide the goal, decision, realistic options, evidence, constraints, and rollback.

Follow `DECIDE` unless newer evidence or higher-priority instructions conflict. If it returns `ESCALATE_TO_USER`, the profile is silent, or safety is uncertain, ask the user. Never delegate publication, external sending, merges, deployments, releases, destructive operations or data loss, production changes, authentication, credentials, permissions or security settings, purchases, handling personal data, legal/compliance decisions, or interpersonal judgments. Preference rules cannot relax these exclusions.

Record only explicit reusable user choices, answers to escalations, and explicit corrections. Never treat silence or continuation as preference evidence. Keep all personal state under `~/.human-distiller/` and never copy it into a project repository.
<!-- human-distiller:end -->
