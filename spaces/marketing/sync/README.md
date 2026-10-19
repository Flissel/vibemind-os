# Marketing Sync

## Stillgelegt: Abgleich DB <-> Markdown (Worker A/B)

Geplant war ein Abgleich in beide Richtungen zwischen Supabase `marketing.*` und
`~/.rowboat/knowledge/Marketing/People/` (Migration 004, Worker A `worker_db_to_fs`, Worker B
`worker_fs_to_db`). Er lief nie im Betrieb: Der Zielordner wurde nie angelegt, und keine der
428 Outbox-Zeilen (02.06.–03.09.2026) wurde abgeholt. Personendaten gehören außerdem nicht in
die unverschlüsselte Rowboat-Ablage.

Am 19.10.2026 hat der User entschieden, ihn stillzulegen (Paket D3):

- Migration `db/076_sync_stilllegen.sql` entfernt die 12 Trigger `trg_emit_sync_*`,
  `emit_sync_event()`, `mark_outbox_applied()`, `_resolve_handle()` und `marketing.sync_outbox`.
  Die Sicherung der Outbox liegt auf der VM unter `~/backups/sync_outbox-vor-076.sql.gz`.
- Die beiden Worker und ihr E2E-Test sind gelöscht. Sie stehen noch in der Git-Historie.
- **Nach 076 nie wieder 004 einspielen.**

Die Einmalskripte für den alten pathx-Postgres (`:5434`) sind ebenfalls weg. pathx existiert
nicht mehr. Die CSV-Abzüge vom Juni liegen in `backups/marketing-snapshots/` im äußeren Repo.

## Was bleibt

| Datei | Rolle |
|---|---|
| `_db.py` | Postgres-Zugriff über `docker exec psql`, unter anderem von `scripts/migration_probe.py` genutzt |
| `worker_imap_sync.py` | Worker C: IMAP nach `marketing.inbound_messages` |
| `inbound_pretag.py` | Erkennt Auto-Antworten (die API nutzt das) |
| `render_md.py`, `_queries.py`, `_frontmatter.py`, `_filename.py` | Markdown-Renderer der alten Worker. Ohne Aufrufer, die eigenen Tests laufen weiter. |

`link_inbound_to_send` (007) setzt weiterhin die Sitzungseinstellung `marketing.sync_origin`.
Ohne Trigger liest sie niemand. Das ist harmlos.
