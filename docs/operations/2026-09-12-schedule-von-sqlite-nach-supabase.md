# schedule von SQLite nach Supabase

**Datum:** 2026-09-12
**Auftrag:** Betreiber — „alles supabase"
**Claim:** `cc-schedule-supabase` im WORKBOARD (`56d0f09`)

## Der Befund, der die Entscheidung trägt

`spaces/schedule/execution.py` führte einen eigenen SQLite-Laden unter
`~/…/spaces/schedule/data/schedule.sqlite3`. Die Tabelle
`public.scheduled_tasks` existiert in der geteilten Supabase aber **seit der
Initial-Migration** (`20260411_init_vibemind.sql:246`) — mit reicherem Schema
(`description`, `execution_mode`, `next_run_at`, `last_run_at`, `run_count`,
`max_runs`, `last_result`, `last_error`, `metadata`) und **leer**.

Es waren also zwei Speicher für dieselbe Sache, und nur einer war für den Rest
des Systems sichtbar. Der `truth:`-Validator von heute früh, das Dashboard,
jede Abfrage von außen — keiner davon sah die SQLite-Datei. Das ist genau die
Drift, gegen die ein gemeinsamer Speicher hilft; die Entscheidung korrigiert
eine bestehende Abweichung, sie führt keine neue ein.

Gemessen vor der Arbeit:

```
scheduled_tasks            0 Zeilen   (psql, nicht nur REST — RLS könnte verbergen)
RLS                        aktiv
Policy                     allow_all_scheduled_tasks, ALL, USING true, CHECK true, PUBLIC
brain-core Env             SUPABASE_URL, SUPABASE_ANON_KEY_FILE   (kein Service-Role)
```

Der Anon-Schlüssel, den brain-core ohnehin hat, darf damit lesen **und**
schreiben. Kein Deploy-Eingriff, keine neue Rolle, kein neues Geheimnis.

## Was geändert wurde

**`ScheduleRepository` spricht PostgREST.** Gleiche öffentliche Form (`get`,
`get_by_idempotency_key`, `list`, `create`, `update`), gleiche Zusagen:
`create` liest nach dem Schreiben frisch zurück und wirft
`schedule create was not persisted`, `update` ebenso. Drei Dinge waren dabei
nicht offensichtlich:

1. **`trigger_config` geht als Objekt raus, nicht als Text.** Die
   SQLite-Fassung musste `json.dumps` machen. Bliebe das, läge ein *String im
   jsonb* — die Spalte wäre formal gefüllt und jeder Filter darauf trotzdem
   blind. Ein Test hält das fest.

2. **Ein PATCH ohne Treffer ist bei PostgREST kein Fehler.** Die
   SQLite-Fassung konnte `cursor.rowcount != 1` prüfen; hier unterscheidet
   erst die Rückfrage danach „geändert" von „es gab nichts". Ohne sie käme ein
   Abbruch auf eine nicht existierende Aufgabe als Erfolg zurück.

3. **Der Schlüssel kommt oft aus einer Datei.** brain-core bekommt ihn als
   Docker-Secret und setzt **nur** `SUPABASE_ANON_KEY_FILE`. Wer allein die
   Variable liest, steht dort ohne Schlüssel da. Gleiche Auflösung wie
   `ideas_client.py:217`. Und: `Authorization: Bearer` nur bei einem echten
   JWT — das lokale Supabase antwortet auf `Bearer anon` mit 401 PGRST301,
   während der `apikey`-Kopf allein durchkommt.

**Die fünf Validatoren hängen auf `truth:supabase_row`.** Die Verengung steht
jetzt direkt im Filter, weil PostgREST mehrere Bedingungen verundet:

| Capability | match |
|---|---|
| `schedule_create` | `id=eq.{result_id}` |
| `schedule_cancel` | `id=eq.{result_id}&status=eq.cancelled` |
| `schedule_snooze` | `id=eq.{result_id}&trigger_type=eq.date` |
| `schedule_openclaw_cron` | `id=eq.{result_id}&trigger_type=eq.cron` |
| `schedule_modify` | `id=eq.{result_id}` — nur Vorhandensein, siehe unten |

`schedule_modify` bleibt schwach, und das steht auch so im YAML: **welches**
Feld geändert wurde, steht nicht im Ergebnistext und ist darum nicht
nachprüfbar. Der Beleg sagt „die Aufgabe existiert danach noch", nicht „sie
wurde geändert".

`on_fail` bleibt bei `report`. Auf `block` anzuheben ist eine eigene
Entscheidung und gehört hinter einen Nachweis, dass die Rückfrage in der
**ausgebrachten** Brain wirklich beobachtet — `world_observer` liest
`SUPABASE_ANON_KEY` direkt und kennt die `*_FILE`-Konvention nicht.

## Der `sqlite_row`-Check ist jetzt ungenutzt

Er wurde heute früh für genau diesen Space gebaut. Nach dem Umzug benutzt ihn
keine Capability mehr. Er bleibt trotzdem stehen: `world_observer` ist ein
Werkzeugkasten, und auch `process_running`/`port_open` werden von keiner
Capability benutzt. Die 14 Tests bleiben gültig. Wer ihn entfernen will, kann
das ohne Folgen tun — es hängt nichts daran.

## Offen: die Migration ist NICHT angewandt

`supabase/migrations/20260912_scheduled_tasks_event_type_idempotency.sql` fügt
die zwei Spalten hinzu, die SQLite hatte und Supabase nicht:

```sql
ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS event_type TEXT DEFAULT 'schedule.create';
ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_sched_idempotency
    ON scheduled_tasks (idempotency_key) WHERE idempotency_key IS NOT NULL;
```

Additiv, auf einer leeren Tabelle, umkehrbar. **Sie ist noch nicht
ausgeführt** — DDL auf der geteilten Datenbank braucht die ausdrückliche
Freigabe des Betreibers, und die Repo-Konvention sagt dasselbe (siehe den Kopf
von `20260817_research_report_artifacts.sql`).

**Bis dahin schreibt schedule nicht.** Ein `INSERT` mit `event_type` läuft
gegen HTTP 400, solange die Spalte fehlt. Das ist der Grund, warum die
Änderung erst nach dem Anwenden zusammenpasst.

## Belege

* Tabelle, Zeilenzahl, RLS und Policy per `psql` im Container gemessen —
  nicht über REST geraten (RLS hätte Zeilen verbergen können).
* Dass der lokale Container dieselbe Instanz ist wie
  `192.168.178.65:54321`: `flowzen_activity` zählt beidseitig 70.
* 15 Tests in `test_schedule_brain_execution.py` grün, davon 5 neu — die
  Fixture steht jetzt auf einem PostgREST-Double, das sich bewusst auch dort
  wie das Original verhält, wo es unbequem ist (ein PATCH ohne Treffer ist
  kein Fehler).
* 43 angrenzende Brain-Tests grün, 158 Space-Tests grün,
  `intake` meldet `contract complete: schedule`.
* **Noch nicht bewiesen:** ein echter Lauf gegen die laufende Supabase. Er
  setzt die Migration voraus.
