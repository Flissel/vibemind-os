# Ideen-Space: was er zeigt

Stand 23.11.2026, Entwurf der Ideen-Sitzung. Noch nicht von Felix bestätigt.
Der Ist-Stand der App wurde hierfür nicht geprüft; das ist die Soll-Beschreibung für die Hülle.

## Aufbau

Zwei Bereiche nebeneinander:

- **Links das Canvas.** Es zeigt die Notizen der offenen Bubble als Karten und die Verbindungen als Linien.
- **Rechts der Chat.** Felix tippt, die KI antwortet knapp. Jede Antwort nennt, was sich am Canvas geändert hat.

Über beiden steht, welche Bubble offen ist. Ohne offene Bubble zeigt das Canvas die Bubbles selbst.

## Was eine Karte zeigt

| Feld | Quelle | Anzeige |
|---|---|---|
| Titel | `canvas_nodes.title` | Kopf der Karte |
| Inhalt | `canvas_nodes.content` | Text, dargestellt nach Format |
| Format | `canvas_nodes.format_schema.type` | Notiz, Tabelle, Aufgabenliste, Pro/Contra, Gliederung und weitere; ohne Wert: Notiz |
| Bewertung | `canvas_nodes.metadata.bewertung.wert` (0 bis 100) | kleine Zahl an der Karte; ohne Wert: nichts |
| Lage | `canvas_nodes.x`, `y` | Position auf dem Canvas |
| Verbindung | `canvas_edges` (`from_node_id`, `to_node_id`, `edge_type`) | Linie zwischen zwei Karten |

Eine Bubble zeigt Titel, Bewertung (`ideas.score`, 0 bis 100) und Zustand (`ideas.status`: `active` oder
`promoted`). Eine beförderte Bubble trägt einen Verweis auf ihr Projekt (`ideas.promoted_to_project_id`).

## Zustände

| Zustand | Wann | Anzeige |
|---|---|---|
| leer | Bubble ohne Notizen | Hinweis „Tippe rechts, um die erste Notiz festzuhalten" |
| vorgemerkt | Schritt ist abgeschickt, Datenbank hat noch nicht bestätigt | Karte sofort da, leicht blass |
| bestätigt | Datenbank hat die Zeile gemeldet | Karte normal |
| abgelehnt | Werkzeug meldet Fehler | Karte verschwindet wieder, Chat nennt den Grund |
| Agent arbeitet | Schritt braucht ein Modell | Marke „arbeitet" an der betroffenen Karte oder Bubble, Chat zeigt „arbeitet" |
| zurückgenommen | „zurück" wurde ausgeführt | Canvas zeigt den Stand davor, Chat nennt die Zahl der Schritte |

## Vorspeicher

Die App hält den Stand der offenen Bubble im Speicher. Ein fester Schritt wird dort sofort eingetragen
(„vorgemerkt") und gleichzeitig abgeschickt. Die Antwort des Werkzeugs oder die Meldung der Datenbank
gleicht ab. Damit das ohne Flackern geht:

- `idea_create` nimmt eine Kennung von der App an (`id`). Die vorgemerkte Karte und die gespeicherte Zeile
  haben so dieselbe Kennung.
- Jedes schreibende Werkzeug gibt die gespeicherte Zeile zurück.
- `canvas_nodes`, `canvas_edges`, `ideas` und `projects` stehen in der Realtime-Veröffentlichung der
  Datenbank. Was ein Agent im Hintergrund ändert, kommt so ohne Nachfragen an.

## Die sechs Tätigkeiten

| Tätigkeit | Sofort sichtbar (fester Aufruf) | Später (Modell) |
|---|---|---|
| festhalten | neue Karte mit dem getippten Text | Titel kürzen, Inhalt ausformulieren |
| formatieren | Karte wechselt die Darstellung | Inhalt passend umschreiben, zum Beispiel Fließtext zu Tabelle |
| ordnen | Karten rücken ins Raster | nach Thema gruppieren |
| verlinken | Linie zwischen zwei genannten Karten; Vorschlag nach gleichen Wörtern | Verbindungen nach Sinn |
| bewerten | genannte Zahl an der Karte oder Bubble | Urteil der KI mit Begründung |
| befördern | Bubble wird Projekt, Zustand „promoted", Verweis auf das Projekt | nichts |

Dazu „zurück": nimmt die letzte Änderung des Agenten als Ganzes zurück.

## Offene Fragen an Felix

1. „Befördern" heißt hier: eine Bubble wird ein Projekt. Soll auch eine einzelne Notiz befördert werden
   können (Notiz wird eigene Bubble)?
2. Bewertung als Zahl 0 bis 100 an der Karte: reicht das, oder soll die Karte auch die Begründung zeigen?
3. Soll der Chat seinen Verlauf je Bubble behalten oder je Sitzung neu beginnen?
