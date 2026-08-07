# Decision journal schema

The local `decision-journal.jsonl` contains one JSON object per explicit reusable decision.

Required fields:

- `schema_version`: integer
- `recorded_at`: ISO 8601 timestamp
- `source`: `user_choice`, `escalation_answer`, or `correction`
- `context`: generalized, non-sensitive description
- `options`: realistic alternatives
- `choice`: explicit user choice
- `reason`: only the reason the user actually stated
- `outcome`: known result or exception condition

Optional fields:

- `prediction`: delegate prediction, when one existed
- `prediction_confidence`: `low`, `medium`, `high`, or `none`

Do not record secrets, credentials, personal data, repository-specific identifiers, or the body of an external message.
