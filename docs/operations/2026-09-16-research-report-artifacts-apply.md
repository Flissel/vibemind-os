# Migration `20260817_research_report_artifacts.sql` — lokal angewendet, fail-closed gemessen

**Datum/Zeit:** 2026-09-16, ca. 12:51–12:54 Uhr (MESZ, +02:00) / 10:51–10:53 UTC
**Autor:** Claude Code (Task 2, `2026-09-16-research-bubble-kopplung`-Plan)
**Zielinstanz:** LOKAL — `http://127.0.0.1:54321`, Docker Swarm, Container-Präfix
`vibemind_supabase-db`. Die Remote-VM (`192.168.178.65` / `100.67.177.45`,
`vibemind-offload-1`) wurde zu keinem Zeitpunkt adressiert, verbunden oder abgefragt.
**Container (Schritt 2):** `vibemind_supabase-db.1.rn53t5e3zha9kvcucnta7qnw8`
**Migrationsdatei:** `supabase/migrations/20260817_research_report_artifacts.sql`,
unverändert angewendet (Blob aus Commit `3be78f6a7637b25141f6e0b826d5531d10a9a2bd`,
2026-08-17). Es wurde keine Zeile der SQL-Datei editiert.

## Wo dieser Eintrag liegt (Branch) — WICHTIG für spätere Leser

**Branch:** `codex/backup-pre-origin-sync-20260825` — **nicht** `master`.

**Verifiziert, nicht angenommen:** `git merge-base --is-ancestor 72daa2a9 master` meldet,
dass der Commit `72daa2a9` (dieser Eintrag) **nicht** von `master` aus erreichbar ist;
`git branch --contains 72daa2a9` listet ausschließlich
`codex/backup-pre-origin-sync-20260825`.

**Warum das so bleibt — bewusste Entscheidung, kein Versehen:** Dieser
`vibemind-os`-Checkout stand bereits vor Beginn dieser Task auf
`codex/backup-pre-origin-sync-20260825`, mit 58 fremden, nicht committeten Änderungen
anderer Sessions im Arbeitsbaum. Das Projekt-`CLAUDE.md` verbietet für dieses Repo, ohne
ausdrückliche Anforderung neue Feature-Branches anzulegen oder zu wechseln, und ein
Wechsel auf `master` hätte diese fremde Arbeit gefährdet. Also: auf dem vorgefundenen
Branch committet — nicht gewechselt, nicht gecherry-pickt, nicht rebased. Diese
Entscheidung wurde geprüft und bestätigt; sie steht nicht zur Diskussion.

**Konsequenz für alle, die diese Notiz später lesen — insbesondere die Task-8-Ausführung:**
Vor Weiterverwendung prüfen, von welchem Branch aus diese Datei gerade gelesen wird. Auf
`master` existiert dieser Eintrag (Stand jetzt) nicht; wer ihn dort braucht, muss Commit
`72daa2a9` gezielt cherry-picken oder gezielt auf `codex/backup-pre-origin-sync-20260825`
nachschlagen, statt anzunehmen, ein `master`-Checkout enthalte automatisch diesen Stand.

Hinweis zu Zugangsdaten: `SUPABASE_ANON_KEY` wurde aus `../.env` in eine Shell-Variable
geladen und nie ausgegeben; `SUPABASE_URL` aus derselben `.env` zeigt auf die Remote-VM
(`http://192.168.178.65:54321`) und wurde deshalb **nicht** benutzt — stattdessen die im
Brief fest vorgegebene lokale URL `http://127.0.0.1:54321`. `SUPABASE_DB_CONTAINER` aus
`.env` (`debian-supabase-db-1`) ist ebenfalls der VM-Containername und wurde ignoriert;
der tatsächlich benutzte Containername kam ausschließlich aus `docker ps --filter
name=vibemind_supabase-db` (Schritt 2).

## Die vier Messwerte

| # | Schritt | Erwartet (Brief) | Gemessen |
|---|---|---|---|
| 1 | Schritt 1 — Ausgangszustand | `vorher=404` | **`vorher=404`** — Treffer |
| 2 | Schritt 4 — `NOTIFY pgrst, 'reload schema'` | PostgREST übernimmt das Schema | **Wirkungslos** — siehe Abweichung unten |
| 3 | Schritt 4 — Erreichbarkeit nach Reload | `nachher=200` | **`nachher=200`**, aber erst nach manuellem `SIGUSR1`-Reload (nicht über den im Brief vorgegebenen `NOTIFY`-Weg) |
| 4 | Schritt 5 — Zero-Citation-INSERT | `ERROR ... violates check constraint "research_report_artifacts_citation_count_check"` | **Exakt dieser Fehler aufgetreten** — Treffer, 0 Zeilen in der Tabelle danach |

## Schritt für Schritt

### Schritt 1 — Ausgangszustand

```
curl -s -o /dev/null -w "vorher=%{http_code}\n" ... /research_report_artifacts?select=id&limit=1
```

Ausgabe: `vorher=404`. Deckt sich mit der Erwartung — die Tabelle existierte vor der
Migration nicht.

### Schritt 2 — Container ermitteln

```
docker ps --filter name=vibemind_supabase-db --format "{{.Names}}"
```

Ausgabe: eine Zeile, `vibemind_supabase-db.1.rn53t5e3zha9kvcucnta7qnw8`.

### Schritt 3 — Migration anwenden

```
docker cp supabase/migrations/20260817_research_report_artifacts.sql "<DB>:/tmp/"
MSYS_NO_PATHCONV=1 docker exec "<DB>" psql -U supabase_admin -d postgres -f /tmp/20260817_research_report_artifacts.sql
```

Tatsächliche Ausgabe (vollständig, in Reihenfolge):

```
BEGIN
CREATE TABLE
CREATE INDEX
CREATE INDEX
CREATE INDEX
CREATE INDEX
CREATE INDEX
COMMENT
COMMENT
COMMENT
COMMENT
COMMENT
ALTER TABLE
psql:...: NOTICE:  policy "allow_all_research_report_artifacts" for relation
  "public.research_report_artifacts" does not exist, skipping
DROP POLICY
CREATE POLICY
DO
COMMIT
NOTIFY
```

Kein `ERROR`. `BEGIN`, `CREATE TABLE`, fünfmal `CREATE INDEX`, `COMMIT` treten wie erwartet
auf. Abweichung vom Brief: die Ausgabe enthält zusätzliche, im Brief nicht einzeln
aufgeführte, aber inhaltlich erwartbare Zeilen aus derselben Datei (`COMMENT` ×5,
`ALTER TABLE` für RLS, eine harmlose `NOTICE` beim `DROP POLICY IF EXISTS`, `DROP POLICY`,
`CREATE POLICY`, der `DO`-Block für die Realtime-Publication, und ein `NOTIFY` — Letzteres
ist die in Zeile 234 der Datei selbst enthaltene `NOTIFY pgrst, 'reload schema';`, die also
schon am Ende von Schritt 3 mitlief, nicht erst in Schritt 4). Das ist kein Fehlschlag,
nur eine unvollständige Aufzählung im Brief.

### Schritt 4 — PostgREST-Cache neu laden und Erreichbarkeit prüfen

Erster Versuch — exakter Befehl aus dem Brief:

```
MSYS_NO_PATHCONV=1 docker exec "<DB>" psql -U supabase_admin -d postgres -c "NOTIFY pgrst, 'reload schema';"
curl ... -> "nachher=%{http_code}\n" ...
```

Ausgabe: `NOTIFY` (Postgres hat die Notification verschickt) gefolgt von **`nachher=404`**
— **Abweichung von der Erwartung `nachher=200`**. Ein zweiter Versuch nach 3s Wartezeit
ergab ebenfalls `nachher_retry=404`.

Diagnose (weil Schritt 4 laut Brief `nachher=200` verlangt und die Migration selbst laut
Schritt 3 sauber ohne `ERROR` durchgelaufen war — die Ursache musste also im
PostgREST-Reload-Weg liegen, nicht in der Migration):

- `docker ps --filter name=vibemind_supabase` zeigt den Container
  `vibemind_supabase-rest.1.kmfbv12c8pfq5tr50hs0b91qf` (PostgREST), `Up 4 hours`,
  0 Restarts, `StartedAt=2026-09-16T07:05:21Z`.
- `docker logs` dieses Containers zeigt einen Verbindungsabbruch der
  LISTEN/NOTIFY-Session um `09:14:19 UTC` ("Failed listening for database
  notifications ... server closed the connection unexpectedly"), gefolgt von einem
  Reconnect der normalen DB-Pool-Verbindung um `09:14:23`–`09:14:31 UTC`
  ("Schema cache loaded 30 Relations, 19 Relationships ..."). Für Schritt 3 selbst
  (Migrationsanwendung) wurde keine eigene Sekunden-genaue Uhrzeit protokolliert; sicher
  ist nur die Reihenfolge: Schritt 3 lief vor dem ersten in dieser Session tatsächlich
  erfassten Zeitstempel, `10:51:41 UTC` (Host-Uhrzeit, während der Schritt-4-Diagnose
  abgefragt). Der `09:14:19 UTC`-Abbruch liegt so oder so klar davor und steht in keinem
  Zusammenhang mit dieser Migration — er ist ein vorbestehender, unabhängiger Zustand der
  lokalen Supabase-Instanz.
- Nach Schritt 3 und dem expliziten `NOTIFY` aus Schritt 4 erschien **kein einziger
  neuer Log-Eintrag** in PostgREST (`docker logs --since 1m` direkt nach einem
  erneuten manuellen `NOTIFY` blieb leer) — der PostgREST-Prozess hat die
  Notification-Kanal-Verbindung nach dem `09:14`-Abbruch offenbar nicht wieder
  sauber abonniert, obwohl der Container weiterlief und `docker ps` ihn als
  gesund/laufend zeigte. `NOTIFY pgrst, 'reload schema'` ist damit in diesem
  Zustand der lokalen Instanz ein stiller No-Op.
- Messung des Container-Uhrzeit-Abgleichs zur Kontrolle: `SELECT now()` im
  DB-Container ergab `2026-09-16 10:51:53+00`, passend zur Host-UTC-Zeit — keine
  Uhrenverschiebung, die die Logs falsch datiert hätte.
- Workaround (PostgREST-eigenes, dokumentiertes Reload-Signal, **keine Änderung an
  der Migrationsdatei oder an Anwendungscode**): `docker kill --signal=SIGUSR1
  vibemind_supabase-rest.1.kmfbv12c8pfq5tr50hs0b91qf`. Direkt danach zeigte
  `docker logs --since 1m`: `Schema cache loaded 31 Relations, 20 Relationships,
  4 Functions, ...` — Relations-Zähler stieg von 30 auf 31, Relationships von 19
  auf 20, genau um die neue Tabelle plus ihre zwei FKs auf `ideas`.
- Erneuter Curl-Check danach: **`nachher_v2=200`**.

Fazit Schritt 4: Die Tabelle ist über REST erreichbar (`200`), aber **nicht über den im
Brief vorgegebenen `NOTIFY`-Weg** erreicht worden, sondern erst über einen manuellen
`SIGUSR1`-Reload des PostgREST-Containers. Das ist ein Infrastrukturbefund über den
Zustand der lokalen PostgREST-Instanz (hängender Notification-Listener nach einem
vorbestehenden Verbindungsabbruch), keine Eigenschaft der Migration selbst und keine
SQL-Änderung. Für Task 8 (VM-Anwendung) relevant: nach dem `NOTIFY` zusätzlich
`nachher`-Status prüfen und bei `404` denselben `SIGUSR1`-Weg (oder einen Neustart des
dortigen PostgREST-Dienstes) als Fallback einplanen, statt sich auf `NOTIFY` allein zu
verlassen.

### Schritt 5 — Fail-closed-Verhalten messen

```
INSERT INTO public.research_report_artifacts
 (artifact_ref, job_id, subject_type, subject_ref, name, citation_count, internal_context_used)
 VALUES ('artifact_v1_00000000000000000000000000','job_v1_00000000000000000000000000',
         'topic','probe','probe.md',0,true);
```

Tatsächliche Ausgabe:

```
ERROR:  new row for relation "research_report_artifacts" violates check constraint "research_report_artifacts_citation_count_check"
DETAIL:  Failing row contains (cdbb82e8-f548-4277-a852-64f926351e27, artifact_v1_00000000000000000000000000, job_v1_00000000000000000000000000, null, topic, probe, null, null, research_report, probe.md, null, markdown, null, null, null, null, 0, t, null, 2026-09-16 10:53:28.74874+00).
```

Exakter Treffer auf die geforderte Erfolgsbedingung. Anschließende Kontrolle
`SELECT count(*) FROM public.research_report_artifacts;` ergab `0` — der fehlgeschlagene
Insert hat keine Zeile hinterlassen (impliziter Transaktions-Rollback von `psql -c`).

Ein Report ohne Zitat ist auf der lokalen Instanz nachweislich nicht speicherbar.

## VM-Anwendung

Die Remote-VM (`192.168.178.65` / `100.67.177.45`, Container `vibemind-offload-1` /
`debian-supabase-db-1`) wurde in dieser Task **nicht** angefasst. Die Anwendung dort ist
Task 8 und braucht eine eigene Autorisierung. Der oben dokumentierte `NOTIFY`-Befund
(PostgREST-Notification-Listener kann nach einem Verbindungsabbruch hängen bleiben) sollte
dort vorab einkalkuliert werden.

## Rollback

Nicht ausgeführt. Die Tabelle ist leer (0 Zeilen, siehe Schritt 5); Rollback-Variante A aus
dem Dateikopf der Migration (`DROP TABLE IF EXISTS ...`) wäre verlustfrei anwendbar, wurde
in dieser Task aber nicht angefordert und deshalb nicht ausgeführt.
