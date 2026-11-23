# Ideen-Space: Absichten für das Verzeichnis

Stand 23.11.2026. Lieferung der Ideen-Sitzung an die Koordination (Auftrag, Abschnitt 8a).
Eintragen kann nur die Koordination (`config/space_agent_registry.yml`).

- **Space:** `ideas`
- **Agent:** `brain-ideas`
- **Server:** `spaces-ideas` (`spaces/ideas/mcp_server.py`)
- **Vorsilbe:** `idea.` (wie heute im Verzeichnis; kein neuer Name)

Alle festen Aufrufe laufen als `execution: { kind: mcp, server: spaces-ideas }` mit
`required_provenance: [approval_ref, cost_ref]`. Schreibende Werkzeuge nehmen `aenderung_id` an.

## Fester Aufruf, kein Modell

| Absicht | Beispielsätze | Werkzeug | Pflichtangaben |
|---|---|---|---|
| `idea.create` (festhalten) | „Halt fest: Preise im Januar prüfen" / „Notiz: Anruf bei der Druckerei" / „Schreib auf, dass der Stand größer sein muss" | `idea_create` | `bubble_id`, `title` |
| `idea.update` (Text ändern) | „Benenne die Notiz Preise in Preisliste um" / „Ergänze bei Druckerei: bis Freitag" | `idea_update` | `id` |
| `idea.format` (formatieren) | „Mach aus der Notiz Preise eine Tabelle" / „Zeig Druckerei als Aufgabenliste" / „Formatiere das als Pro und Contra" | `idea_format` | `id`, `format` |
| `idea.arrange` (ordnen) | „Räum das Canvas auf" / „Ordne die Notizen im Raster" / „Sortiere die Karten nach Bewertung" | `idea_arrange` | `bubble_id` |
| `idea.connect` (verlinken) | „Verbinde Preise mit Druckerei" / „Verlinke die beiden Notizen" | `idea_connect` | `from_id`, `to_id` |
| `idea.auto_link` (verlinken, Vorschlag) | „Verlinke, was zusammengehört" / „Finde Verbindungen in dieser Bubble" | `idea_auto_link` | `bubble_id` |
| `idea.rate` (bewerten, Zahl) | „Bewerte Preise mit 80" / „Gib der Notiz Druckerei 40 Punkte" | `idea_rate` | `id`, `score` |
| `bubble.rate` (bewerten, Zahl) | „Bewerte die Bubble Messe mit 70" | `bubble_rate` | `id`, `score` |
| `bubble.promote` (befördern) | „Mach aus der Bubble Messe ein Projekt" / „Befördere diese Bubble" | `bubble_promote` | `bubble_id` |
| `idea.list` (lesen) | „Zeig die Notizen dieser Bubble" / „Was steht hier?" | `idea_list` | keine (`bubble_id` empfohlen) |
| `idea.aenderung_zurueck` | „Zurück" / „Nimm das zurück" | `aenderung_zurueck` | keine |

Die Werkzeuge verlangen Kennungen, keine Namen. Der Name aus dem Satz („die Notiz Preise") wird vorher
aufgelöst: aus dem Vorspeicher der App oder über `idea_list`. Das ist ein Lesen ohne Modell.

## Braucht ein Modell (Auftrag an `brain-ideas`, im Hintergrund)

| Absicht | Beispielsätze | Was das Modell entscheidet | Schreibt über |
|---|---|---|---|
| `idea.rewrite` | „Formuliere die Notiz aus" / „Fass die Notiz zusammen" / „Bau aus dem Text eine echte Tabelle" | den neuen Text | `idea_update`, `idea_format` |
| `idea.group` | „Ordne die Notizen nach Thema" / „Was gehört zusammen?" | die Gruppen | `idea_update` (Lage), `idea_connect` |
| `idea.evaluate` | „Was hältst du von dieser Notiz?" / „Bewerte die Bubble" (ohne Zahl) | Urteil und Begründung | `idea_rate`, `bubble_rate` |

Für diese drei gilt die 2-Sekunden-Grenze für die erste Reaktion: Die App zeigt sofort „arbeitet"; das
Ergebnis kommt später über die Datenbank an.

## Was dafür im Verzeichnis zu ändern ist

1. `ideas.mcp_tools.spaces-ideas` erweitern um: `idea_create`, `idea_update`, `idea_list`, `idea_format`,
   `idea_arrange`, `idea_auto_link`, `idea_rate`, `bubble_rate`, `bubble_promote`.
2. Die Ereignisse `idea.create`, `idea.update`, `idea.list`, `idea.auto_link` und die `idea.format_*` vom
   breiten Datenbank-Server auf `spaces-ideas` umbinden; `idea.format`, `idea.arrange`, `idea.rate`,
   `bubble.rate` neu eintragen.
3. Den Agenten `brain-ideas` in OpenFang mit derselben Werkzeugliste neu erzeugen.

Die neuen Werkzeuge `idea_format`, `idea_arrange`, `idea_auto_link`, `idea_rate`, `bubble_rate` baut die
Ideen-Sitzung in `spaces/ideas/mcp_server.py`.
