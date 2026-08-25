# Learning Migration Map

The legacy source is `C:/Users/User/Desktop/Learning_plattform`. It remains
read-only throughout inventory, export, transform, and validation. A real import
or route switch requires separate action-time confirmation.

| Legacy data | Canonical destination | Export rule |
| --- | --- | --- |
| universities, study programs | LearnHouse local organization context | Preserve source IDs; do not migrate auth credentials |
| modules | LearnHouse courses | One immutable imported course revision per migration batch |
| topics, hashtags | Chapters and adaptive concepts | Preserve module/topic ancestry and source IDs |
| knowledge documents | Learning sources and source revisions | Re-hash content and create Learning-owned artifact metadata |
| knowledge chunks | Rebuilt source chunks and Qdrant points | Export text and locators only; never trust legacy vectors or Qdrant point IDs |
| questions, answers, tasks | Activities, adaptive items, expected answers, rubrics | Unsupported formats are quarantined during transformation |
| quizzes, attempts, submissions, evaluations | Sessions, responses, evaluations, initial mastery evidence | Normalize scores to `[0,1]` and retain actor/source provenance |
| browser mistake journal | Review schedules and misconceptions | Import only explicit exported entries; absent browser state is reported |
| local artifacts | Learning-owned immutable artifacts | Export relative locator, byte count, media type, and SHA-256 only |

The deterministic export format consists of `records.jsonl` and
`manifest.json`. Every record contains `source_system`, `source_id`,
`migration_batch_id`, and a canonical payload hash. Provider credentials,
legacy embeddings, Qdrant point IDs, and host-absolute artifact paths are not
part of the export contract.

## Read-Only Inventory, 2026-08-25

The stopped legacy PostgreSQL volume was mounted read-only, copied into a
temporary isolated volume, and queried only through that copy. The temporary
container and volume were removed after export. The original PostgreSQL volume
was 47.5 MiB and was not mutated.

Batch `legacy-real-13845e47` exported 50 records:

| Entity | Count |
| --- | ---: |
| universities / study programs | 2 / 2 |
| professors / users | 3 / 6 |
| modules / topics | 2 / 2 |
| questions / answers / tasks | 5 / 8 / 5 |
| quizzes / quiz questions | 3 / 3 |
| attempts / answer submissions / evaluations | 3 / 3 / 3 |

The current database contains zero hashtags, open questions, open answers,
knowledge documents, knowledge chunks, and moderation reports. The checked-in
legacy Qdrant snapshot directory contains no files. No browser-local mistake
journal file was found, so browser-only state is not claimed as exported. No
legacy vectors or provider credentials were read into the export.
