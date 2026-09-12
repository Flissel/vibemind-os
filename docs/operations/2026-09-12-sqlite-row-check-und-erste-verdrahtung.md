# Der `sqlite_row`-Check und die erste Verdrahtung vorhandener Rückfragen

**Datum:** 2026-09-12
**Geändert:** `brain/the_brain/core/world_observer.py` (neuer Check),
`brain/the_brain/data/capabilities.yaml` (6 Validatoren),
`coding-engine/.../space_contract.py` (Liste nachgezogen)

Die Bilanz von heute früh ergab: 54 schreibende Capabilities, 27 mit
`truth:`-Validator, und die 27 liegen komplett in `ideas` und `bubbles`.
Der naheliegende Schluss wäre gewesen, 27 Validatoren nachzutragen. Die
Messung hatte aber etwas anderes gezeigt — **bei drei Spaces existiert die
unabhängige Rückfrage bereits, nur innerhalb der Operation.** Dieser Schritt
macht zwei davon nach außen sichtbar.

## Ergebnis

| | vorher | nachher |
|---|---|---|
| Lücken gesamt | 29 | **23** |
| vollständige Spaces | rowboat | rowboat, **flowzen**, **schedule** |

## Was gebaut wurde

### `sqlite_row` — der neunte Check

`schedule` schreibt nach SQLite, nicht nach Supabase. Keiner der acht
vorhandenen Checks konnte eine SQLite-Zeile lesen, also war die vorhandene
Rücklesung (`ScheduleRepository.update`/`create` lesen nach dem Schreiben
frisch) von außen nicht zu belegen. Der neue Check fragt die Datei direkt.

Zwei Entwurfsentscheidungen, beide aus der Sache heraus:

1. **Werte werden immer gebunden, nie interpoliert.** Der Wert stammt aus
   einem Regex über den Ergebnistext der Operation — also von außen
   beeinflussbar. Tabellen- und Spaltennamen müssen einem strengen
   Bezeichner-Muster genügen. Zwei Tests prüfen das ausdrücklich:
   `id=x' OR '1'='1` wird als *Wert* gebunden und findet nichts (REFUTED,
   kein Durchschlupf), ein Tabellenname mit `; DROP TABLE` wird abgewiesen,
   und die Tabelle ist danach nachweislich unversehrt.

2. **`expect_column`/`expect_value` prüfen die Wirkung, nicht nur die
   Existenz.** Ohne das würde ein `cancel`, das den Status gar nicht gesetzt
   hat, als Erfolg durchgehen, solange die Zeile noch da ist. Eine Kontrolle
   zeigt genau das: `cancel` gegen eine noch aktive Aufgabe →
   `REFUTED: no row for id=… and status=cancelled`.

Nicht nachsehen können ist nie ein Fehlschlag der Handlung: fehlende Datei,
unbekannte Tabelle, leerer Platzhalter → UNVERIFIED, nie REFUTED.
14 Tests in `brain/the_brain/tests/test_world_observer_sqlite_row.py`.

### Sechs Validatoren

* `schedule_create` — Zeile mit `id={result_id}` vorhanden. Genau der
  Fehlermodus, der am 11.09. wirklich auftrat (create war nicht persistiert).
* `schedule_cancel` — zusätzlich `status=cancelled`.
* `schedule_snooze` — zusätzlich `trigger_type=date` (snooze schreibt den
  Auslöser um, `execution.py:278`).
* `schedule_openclaw_cron` — zusätzlich `trigger_type=cron`.
* `schedule_modify` — nur Vorhandensein. **Welches** Feld geändert wurde,
  steht nicht im Ergebnistext und ist darum nicht nachprüfbar; das steht so
  auch im YAML-Kommentar, damit niemand den Beleg für mehr hält als er ist.
* `rose.accept` — `flowzen_activity` mit
  `event_type=eq.recommendation_accepted:{result_id}`.

## Zwei bewusst unterschiedliche `on_fail`-Stufen

**`rose.accept` behält `block`.** Dort stand schon ein blockierendes Tor
(`rule:flowzen_result`), das aber nur den **Selbstbericht** des Umschlags
prüfte. Ein bloß meldender Check wäre eine Absenkung gewesen — der Executor
reicht ein `ok: False` nur durch, der Validator ist die Stelle, die daraus
einen Abbruch macht. Der Schutz bleibt also erhalten und ist jetzt belegt:
fällt der Schreibvorgang aus, trägt der Umschlag keine id, der Platzhalter
bleibt ungefüllt, die Beobachtung ist UNVERIFIED und damit ungültig.

**Die fünf `schedule_*` bekommen `report`.** Dort stand bisher *gar kein*
Validator, `report` ist also reiner Gewinn. `block` wäre hier riskant: der
Vorgabepfad der SQLite-Datei ist in der Ausbringung unbewiesen
(`SCHEDULE_DB_PATH` ist nirgends produktiv gesetzt, und die Datei existiert
im Arbeitsbaum nicht). Ein blockierendes Tor auf einem ungeklärten Pfad legt
Planung lahm, statt sie zu sichern.

## Belege

Die Kette wurde **echt durchlaufen**, nicht nur komponentenweise geprüft:
Aufgabe über `execution.create` angelegt → Ergebnis durch den echten
`CapabilityRouter` und `CapabilityValidator` → Beobachtung gegen die
SQLite-Datei.

```
schedule_create  valid=True   VERIFIED: sqlite: row present for id=584041d4-…
schedule_cancel  valid=True   VERIFIED: sqlite: row present for id=… and status=cancelled
schedule_create  valid=False  REFUTED  (erfundene id — Gegenprobe)
cancel auf aktiver Aufgabe    REFUTED: no row for id=… and status=cancelled
cron-Erwartung gegen date     REFUTED
```

Alle sechs Capabilities erreichen den echten Router mit ihrem Validator
(`CapabilityRouter.get_capability`) — die Falle, an der das am 08.09. schon
einmal scheiterte (Einträge ohne `match_patterns` überspringt der Router).

Für `rose.accept` wurde die Rückfrage gegen die **laufende** Supabase
geprüft: `flowzen_activity` ist lesbar (HTTP 200), und der Filter mit dem
Doppelpunkt im Wert wird von PostgREST angenommen (200 mit leerer Menge für
einen nicht existierenden Wert — also wohlgeformt, kein Syntaxfehler).

Tests: 14 neu, 37 in den angrenzenden Brain-Tests grün, 158 Space-Tests grün.

## Was die Zahl 23 NICHT sagt

Der Intake misst, ob ein `truth:`-Validator **deklariert** ist — nicht, ob er
in Produktion etwas beobachtet. Für `schedule` ist der DB-Pfad dort
unbewiesen; solange `SCHEDULE_DB_PATH` nicht gesetzt ist, kann die Rückfrage
`sqlite file not found (cannot verify)` liefern und stillschweigend
UNVERIFIED bleiben. **Nächster konkreter Schritt: `SCHEDULE_DB_PATH` in der
Ausbringung setzen**, dann trägt der Beleg auch dort.

Zweite Falle, teuer gelernt: `GROUND_TRUTH_ENABLED` wird **beim Import** in
eine Modulkonstante gelesen (`world_observer.py:45`). Wer die Variable erst
nach dem Import exportiert, bekommt stillschweigend gar keine Beobachtung —
jeder `truth:`-Validator liefert dann UNVERIFIED.

## Was offen bleibt

`video` (6 Lücken) braucht weiter einen eigenen Check-Typ: `execute_video`
kehrt sofort mit `accepted` zurück, der Artefakt-Beleg landet im
Job-Datensatz. Ein `file_exists` direkt danach schlüge fehl. Der Check müsste
den Job-Beleg nachschlagen.

`mirofish` (4) hat für alle vier schreibenden Operationen bereits eine
GET-Route zum Nachschlagen (`/api/graph/task/{id}`, `/api/simulation/{id}/
run-status`, `/api/report/{id}`) — dort wäre `truth:http_ok` zu schwach,
gebraucht würde ein Check, der den Status im JSON liest.
