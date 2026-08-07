# Security and privacy

Human Distiller stores preference answers locally because they may reveal personal working habits. Do not commit or paste a real `decision-profile.md`, `decision-journal.jsonl`, `onboarding-state.json`, `install-state.json`, or backup archive into an issue or pull request.

Report suspected vulnerabilities privately to the repository owner. Do not open a public issue before a fix or mitigation has been agreed. The repository's supply-chain controls are tracked in [`docs/security-baseline.md`](docs/security-baseline.md).

When reporting a problem:

1. Reproduce it with a temporary `--state-dir`.
2. Replace real answers, usernames, paths, repository names, and identifiers with synthetic values.
3. Remove credentials and other secrets completely; redaction alone may still reveal their shape.

The plugin never delegates publication, external sending, merges, deployments, releases, destructive operations or data loss, production changes, authentication, credentials, permissions or security settings, purchases, handling personal data, legal/compliance decisions, or interpersonal judgments. Preference rules cannot relax these exclusions.
