# Ideen-Space: Zeiten, gemessen am 23.11.2026

Frage: Welche Schritte gehen ohne Modell, und senkt ein dauerhaft offener Agent die Zeit?

## Ergebnis in drei Sätzen

- Ein dauerhaft offener Agent zeigt das erste Zeichen nach 0,8 s und ist nach 2,2 bis 2,9 s fertig. Je Satz
  neu gestartet braucht derselbe Agent 3,8 bis 4,5 s, über OpenFang 6,6 bis 8,1 s.
- Die 2-Sekunden-Grenze für die erste Reaktion hält ein offener Agent also ein. Ein je Satz neu gestarteter
  hält sie nie ein.
- Feste Schritte brauchen kein Modell. Lesen über das Gehirn dauert 0,04 s.

## Messwerte

| Weg | erstes Zeichen | fertig | Läufe |
|---|---|---|---|
| Satz an `brain-ideas` über OpenFang (Messung der Koordination) | nicht gemessen | 6,6 bis 8,1 s | – |
| Claude Haiku, je Satz neu gestartet, ohne Werkzeug-Dienste und ohne Nutzer-Einstellungen | nicht gemessen | 4,49 s und 3,83 s | 2 |
| Claude Haiku, ein offener Prozess, erster Satz | 1,79 s | 2,98 s | 1 |
| Claude Haiku, ein offener Prozess, Satz 2 bis 4 | 0,78 bis 0,83 s | 2,16 bis 2,92 s | 3 |
| Lesen `GET /api/bubbles/list` am Gehirn | – | 0,04 s | 1 |

Der offene Prozess lief so: `claude -p --model haiku --strict-mcp-config --setting-sources project
--input-format stream-json --output-format stream-json`, Sätze nacheinander in dieselbe Eingabe. Die Sätze
waren Ein-Wort-Antworten ohne Werkzeug.

## Offener Agent mit Werkzeug-Aufruf

Der echte Fall: ein offener Prozess, der Ideen-Server als Werkzeug-Dienst, je Satz ein Aufruf. Die Datenbank
war nachgestellt (lokaler Dienst, antwortet in Millisekunden); echte Daten wurden nicht berührt. Ein Lauf mit
vier Sätzen.

| Satz | erste Reaktion | Werkzeug gewählt | Werkzeug fertig | Antwort fertig |
|---|---|---|---|---|
| 1 „Halt fest: Anruf bei der Druckerei" (`idea_create`) | 1,59 s | 4,80 s | 4,99 s | 8,51 s |
| 2 „Mach aus der Notiz Preise eine Tabelle" (`idea_format`) | 0,64 s | 1,83 s | 2,04 s | 6,44 s |
| 3 „Bewerte Preise mit 80" (`idea_rate`) | 0,75 s | 1,17 s | 1,34 s | 4,62 s |
| 4 „Halt fest: Stand muss groesser sein" (`idea_create`) | 0,65 s | 1,28 s | 1,48 s | 4,23 s |

- „Werkzeug fertig" ist der Zeitpunkt, an dem die Karte am Canvas steht. Ab dem zweiten Satz sind das 1,3 bis
  2,0 s. Der erste Satz nach dem Start braucht 5,0 s.
- Der Aufruf des Werkzeugs selbst dauert 0,2 s. Mit echter Datenbank kommen je Abfrage einige
  Hundertstelsekunden dazu.
- Der Schlusssatz des Agenten kostet weitere 2,7 bis 4,4 s. Die App sollte nicht auf ihn warten, sondern die
  Änderung zeigen, sobald das Werkzeug fertig ist.
- Der Agent hat in allen vier Sätzen das richtige Werkzeug gewählt. Die Kennungen standen in seinem Auftrag.

## Was das heißt

1. **Der Start kostet am meisten.** Zwischen 6,6 s (über OpenFang) und 3,8 s (schlank, direkt) liegen die
   Start-Kette (Batch, PowerShell, Python, Claude) und das Laden der Werkzeug-Dienste. Ein offener Prozess
   spart beides.
2. **Die Antwort selbst braucht rund 2 s.** Auch offen ist ein Satz erst nach 2,2 bis 2,9 s fertig. Feste
   Schritte müssen deshalb am Modell vorbei.
3. **Aufteilung:** festhalten, Format wechseln, ins Raster ordnen, zwei Karten verbinden, Zahl vergeben,
   befördern und zurück sind feste Aufrufe (`ABSICHTEN.md`). Umschreiben, nach Thema ordnen und Urteilen
   brauchen das Modell und laufen im Hintergrund.

## Nicht gemessen

- Ein fester Schreib-Aufruf über OpenFang (`POST /mcp` im Namen des Agenten). Dafür braucht es Schlüssel und
  Freigabe-Verweise, die nur das Gehirn erzeugt.
- Der offene Agent gegen die echte Datenbank und mit mehr als einem Lauf.
- Ob OpenFang einen Agenten überhaupt offen halten kann. Im Repo gibt es dafür keinen Aufruf.
- Mehr als vier Sätze je Weg; die Streuung ist also nicht bekannt.

An `brain-ideas` gingen für diese Messung keine Sätze. Das Tagesbudget ist unberührt.
