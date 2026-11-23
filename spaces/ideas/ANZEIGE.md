# Ideen-Space: was er zeigt

Stand 23.11.2026, Entwurf der Ideen-Sitzung. Felix hat drei Fragen dazu beantwortet (siehe unten); der Rest
ist noch nicht von ihm bestätigt.
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

## Kontext, den der Space mitgeben kann

Ein Satz nennt Namen („die Notiz Preise"), die Werkzeuge verlangen Kennungen. Die Auflösung macht die
Abkürzung im Gehirn (Aufgabe der Koordination). Der Ideen-Space kann dafür je Satz mitgeben:

Die Feldnamen sind mit der Hülle abgestimmt (23.11., Entwurf). Sie reisen als `context` an der
Chat-Nachricht der App mit.

| Feld | Inhalt | Quelle |
|---|---|---|
| `space` | `ideas` | fest |
| `bubble` | `{id, title}` der offenen Bubble, oder `null` | Vorspeicher der App |
| `cards` | je sichtbarer Karte `{id, title, format, score}`, höchstens 100 | Vorspeicher der App |
| `cards_total` | Zahl aller Karten der Bubble, falls `cards` gekürzt ist | Vorspeicher der App |
| `selection` | Kennungen der markierten Karten, in der Reihenfolge des Markierens | App |
| `last_id` | Kennung der Karte, die zuletzt angelegt oder geändert wurde | Vorspeicher der App |
| `bubbles` | nur ohne offene Bubble: `{id, title}` der sichtbaren Bubbles | Vorspeicher der App |
| `new_id` | von der App vergebene Kennung für eine neue Karte (`idea_create` mit `id`) | App |

Damit lassen sich auflösen: „die Notiz Preise" (Titel in `cards`), „die beiden" (`selection`), „das" oder
„die letzte" (`last_id`), „diese Bubble" (`bubble`). Passt ein Name auf mehrere Karten oder auf keine, fragt
der Chat zurück und rät nicht.

Die Antwort an die App nennt die gespeicherten Zeilen, damit sie abgleichen kann:

| Feld | Inhalt |
|---|---|
| `changes` | je Änderung `{op, id, row}`; `op` ist `create`, `update`, `delete`, `link` oder `promote` |
| `changes[].pending_id` | bei `create`: das `new_id`, das diese Zeile einlöst |
| `change_id` | Kennung der Änderung (`aenderung_id`); dieselbe nimmt „zurück" entgegen |
| `working` | `true`, wenn ein Modell noch weiterarbeitet |
| `targets` | bei `working`: Kennungen der Karten oder der Bubble, an denen gearbeitet wird |

`idea_create` legt keinen zweiten Eintrag mit gleichem Titel in derselben Bubble an, sondern gibt die
vorhandene Zeile zurück. Dann ist `id` ungleich `pending_id`: Die App entfernt die vorgemerkte Karte und
hebt die vorhandene hervor.

Vormerken vor dem Abschicken **[Wahl]**: Die App merkt nur vor, wenn der Satz mit einem klaren Auftakt
beginnt („Notiz:", „notiere", „halt fest", „halte fest", „schreib auf", „merk dir"). Alles andere zeigt im
Chat „arbeitet" und wartet auf die Antwort. So entsteht aus einer Frage keine falsche Karte.

## Die sechs Tätigkeiten

| Tätigkeit | Sofort sichtbar (fester Aufruf) | Später (Modell) |
|---|---|---|
| festhalten | neue Karte mit dem getippten Text | Titel kürzen, Inhalt ausformulieren |
| formatieren | Karte wechselt die Darstellung | Inhalt passend umschreiben, zum Beispiel Fließtext zu Tabelle |
| ordnen | Karten rücken ins Raster | nach Thema gruppieren |
| verlinken | Linie zwischen zwei genannten Karten; Vorschlag nach gleichen Wörtern | Verbindungen nach Sinn |
| bewerten | genannte Zahl an der Karte oder Bubble | Urteil der KI; die Begründung steht extra (siehe „Begründungen") |
| befördern | Bubble wird Projekt, Zustand „promoted", Verweis auf das Projekt; nur ganze Bubbles | nichts |

Dazu „zurück": nimmt die letzte Änderung des Agenten als Ganzes zurück.

## Begründungen

Die Begründung einer Bewertung steht nicht an der Karte. **[Felix, 23.11.]** Sie steht in einer eigenen Liste
zur offenen Bubble, zum Beispiel als Abschnitt im Chat-Bereich.

| Feld | Quelle | Anzeige |
|---|---|---|
| Text | `ideas.metadata.bewertungen[].text` | der Begründungstext |
| Karten | `ideas.metadata.bewertungen[].karten` (Kennungen aus `canvas_nodes`) | je Karte ein Link mit ihrem Titel; ein Klick springt zur Karte und hebt sie hervor |
| Zahl | `ideas.metadata.bewertungen[].wert` (0 bis 100, kann fehlen) | neben dem Text |
| Zeitpunkt | `ideas.metadata.bewertungen[].erstellt_am` | klein, neueste zuerst |

Geschrieben wird der Eintrag mit `bewertung_begruenden`. Das Werkzeug nimmt nur Karten der eigenen Bubble an.
Eine Bubble hält die 50 neuesten Begründungen. Wird eine verlinkte Karte später gelöscht, zeigt der Link
„Karte gelöscht" und springt nicht.

## Chat-Verlauf

Der Chat hat je Bubble einen eigenen Verlauf. **[Felix, 23.11.]** Wechselt Felix die Bubble, wechselt der
Verlauf mit. Ohne offene Bubble gibt es einen Verlauf für die Übersicht.

Wo der Verlauf gespeichert wird und ob er einen Neustart der App übersteht, ist noch nicht festgelegt; das
klären Hülle und Koordination. Für einen dauerhaft offenen Agenten heißt das: Er braucht je Bubble einen
eigenen Gesprächsfaden, oder der Verlauf der Bubble reist mit dem Satz mit.

## Entschieden von Felix (23.11.)

1. Befördert werden nur ganze Bubbles, keine einzelnen Notizen.
2. Die Begründung einer Bewertung steht nicht an der Karte, sondern extra, mit Links zu den Karten.
3. Der Chat hat je Bubble einen eigenen Verlauf.
