# Learning Migration Dry Run: READY

- Source system: `learning_plattform_v1`
- Migration batch: `legacy-real-13845e47`
- Export records: **50**
- Canonical envelopes: **32**
- Warnings: **0**
- Quarantines: **0**
- Destructive operations: **0**

## Checks

| Check | Status |
| --- | --- |
| `export_records_hash` | passed |
| `export_record_count` | passed |
| `export_entity_counts` | passed |
| `source_content_hashes` | passed |
| `envelope_content_hashes` | passed |
| `canonical_foreign_keys` | passed |
| `score_ranges` | passed |
| `artifact_existence` | passed |
| `quarantine_empty` | passed |
| `destructive_operations` | passed |

## Counts

### Legacy export

- `answers`: 8
- `app_users`: 6
- `evaluations`: 3
- `modules`: 2
- `professors`: 3
- `questions`: 5
- `quiz_answer_submissions`: 3
- `quiz_attempts`: 3
- `quiz_questions`: 3
- `quizzes`: 3
- `study_programs`: 2
- `task_catalog_items`: 5
- `topics`: 2
- `universities`: 2

### Canonical import

- `activity`: 5
- `adaptive_item`: 5
- `chapter`: 2
- `concept`: 2
- `course`: 2
- `evaluation`: 3
- `mastery_evidence`: 3
- `organization`: 2
- `program`: 2
- `response`: 3
- `session`: 3

## Blockers

- None

## Warnings

- None

## Non-Claims

- `real_import_not_executed`
- `qdrant_rebuild_not_executed`
- `route_cutover_not_executed`
- `legacy_data_not_modified`
