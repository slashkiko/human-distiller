# Security baseline status

This repository adopts OpenSSF OSPS Baseline `v2026.02.19` as its security requirements catalogue. A version change requires a review of the upstream control delta and an update to this document in the same pull request.

| Control area | Implementation |
| --- | --- |
| Dependency provenance | mise lockfile, provenance verification, and seven-day release age |
| GitHub Actions | full commit-SHA pinning, Pinact, zizmor, and least-privilege permissions |
| Secret handling | Betterleaks full-history scanning and repository privacy tests |
| Personal state | Git-worktree write guard, restrictive local permissions, and synthetic-only fixtures |
| Review | CODEOWNERS, pull-request review, and no automatic dependency merge |
| Dependency updates | Renovate pull requests with no automatic merge |
