# Desktop-Space: Absichten für das Verzeichnis des Gehirns

Stand: 23.11.2026. Lieferung der Desktop-Sitzung an die Koordination, in der Form von
`config/space_agent_registry.yml`. Eintragen kann nur die Koordination.

## Vorschlag für den Block `desktop`

```yaml
desktop:
  agent: brain-desktop
  prefixes: [desktop.]
  keywords: ["screenshot|bildschirm|desktop|klick|click|tippe|fenster"]
  enabled: true
  description: "Aufgaben am echten Bildschirm: sehen, klicken, tippen, mit Aufgabenbuch."
  mcp_servers: [spaces-desktop]
  mcp_tools:
    spaces-desktop:
      - desktop_status
      - desktop_screen_read
      - desktop_elements
      - desktop_find
      - desktop_screenshot
      - desktop_task_start
      - desktop_task_step
      - desktop_task_finish
      - desktop_task_status
  events:
    desktop.status:      { tool: desktop_status,      required_params: [],     execution: { kind: mcp, server: spaces-desktop } }
    desktop.read:        { tool: desktop_screen_read, required_params: [],     execution: { kind: mcp, server: spaces-desktop } }
    desktop.screenshot:  { tool: desktop_screenshot,  required_params: [],     execution: { kind: mcp, server: spaces-desktop } }
    desktop.task.status: { tool: desktop_task_status, required_params: [],     execution: { kind: mcp, server: spaces-desktop } }
    desktop.task.stop:   { tool: desktop_task_finish, required_params: [task_id, state], execution: { kind: mcp, server: spaces-desktop } }
    desktop.task:        { tool: desktop_task_start,  required_params: [task], execution: { kind: mcp, server: spaces-desktop },
                           requires_confirmation: true, result_validation: external_receipt }
```

## Die Absichten einzeln

| Absicht | Beispielsätze | Werkzeug, Pflichtangaben | Fester Aufruf oder Modell |
|---|---|---|---|
| `desktop.status` | "Läuft der Desktop-Dienst?" / "Was macht der Desktop gerade?" / "Wie viele Monitore sind dran?" | `desktop_status`, keine | fest |
| `desktop.read` | "Was steht gerade auf dem Bildschirm?" / "Lies den Bildschirm vor." | `desktop_screen_read`, keine | fest |
| `desktop.screenshot` | "Mach einen Screenshot." / "Bildschirmfoto vom zweiten Monitor." | `desktop_screenshot`, optional `monitor` | fest |
| `desktop.task.status` | "Wie weit ist die Desktop-Aufgabe?" / "Was hat der Desktop-Agent zuletzt getan?" | `desktop_task_status`, optional `task_id` | fest |
| `desktop.task.stop` | "Brich die Desktop-Aufgabe ab." / "Stopp, nicht weiterklicken." | `desktop_task_finish`, `task_id`, `state: abgebrochen` | fest (`task_id` aus `desktop_task_status`) |
| `desktop.task` | "Öffne den Editor und schreibe Hallo." / "Klick im Browser auf Anmelden." / "Scroll in dem Fenster nach unten und lies mir den letzten Absatz vor." | Schritt 1: `desktop_task_start`, `task`. Schritt 2: Auftrag an `brain-desktop` mit `task_id` und `task` | Schritt 1 fest, Schritt 2 **Modell** |

Einzelne Handgriffe ("klick auf X", "tippe Y", "drücke Enter") sind keine eigenen Absichten
mehr, sondern `desktop.task`. Ein Klick braucht immer erst einen Blick auf den Bildschirm,
also ein Modell, und soll im Aufgabenbuch stehen.

## `desktop.task` als Plan mit zwei Schritten

1. `mcp:brain-desktop:spaces-desktop:desktop_task_start` mit `task` → liefert `id`.
   Ab hier zeigt die App "läuft" (Abnahmepunkt 6).
2. `openfang:brain-desktop` mit der Eingabe `{task_id, task}`. Der Agent arbeitet nur mit
   seinen Werkzeugen und schließt mit `desktop_task_finish`.
3. Nachprüfung am Zustand: `desktop_task_status(task_id)` → `zustand` ist `fertig`, jeder
   Schritt `ok: true`, kein Schritt mit `meldung` "trocken". Nicht am Text der Antwort.

## Anweisung an `brain-desktop` (Vorschlag für `prompt_hint`)

```
Du führst genau eine Aufgabe am echten Bildschirm aus. Du bekommst task_id und task.
1. Sieh zuerst hin: desktop_elements (aktives Fenster, Bedienelemente mit Klickpunkt),
   dazu desktop_screen_read für Text und desktop_find(text) für eine einzelne Stelle.
2. Führe je Aufruf genau einen Schritt aus: desktop_task_step(task_id, action, ..., why).
   why ist ein kurzer Satz für den Menschen, der zusieht.
3. Sieh nach jedem Schritt wieder hin, bevor du den nächsten wählst. Das Werkzeug setzt das
   durch: Ein Schritt ohne desktop_elements, desktop_screen_read oder desktop_find seit dem
   letzten Schritt wird mit nicht_hingesehen abgelehnt. Dann sieh hin und versuche es erneut.
4. Schließe immer ab: desktop_task_finish(task_id, state, result). state ist fertig,
   fehlgeschlagen oder abgebrochen. result ist ein Satz.
Regeln: Klicke nie auf Senden, Kaufen, Löschen oder Bestätigen, wenn die Aufgabe das nicht
wörtlich verlangt. Tippe keine Passwörter. Meldet ein Schritt not_aus, höre sofort auf und
schließe mit abgebrochen. Findest du das Ziel nach drei Versuchen nicht, schließe mit
fehlgeschlagen und sag, was du gesehen hast.
```

Modellklasse: Vorschlag `sonnet` statt heute `haiku`. Ein falscher Klick am echten Rechner
ist teurer als ein langsamer Lauf. Entscheidung bei Koordination und Felix.

## Was die Koordination dafür tun muss

1. Den Werkzeug-Dienst in OpenFang eintragen: Name `spaces-desktop`, stdio,
   `python <vibemind-os>/spaces/desktop/mcp_server.py`. Umgebung:
   `DESKTOP_AUTOMATION_URL=http://127.0.0.1:8007`. Heute kennt OpenFang nur `vibemind-db`,
   `laura` und `webapp-alerts`; den im Verzeichnis genannten Server `desktop-automation`
   gibt es dort nicht.
2. Den Block oben ins Verzeichnis eintragen und die Agenten neu erzeugen. Heute hat
   `brain-desktop` nur `memory_store` und `memory_recall` und kann nichts am Bildschirm tun.
3. **Nur mit Felix' Ja:** `VIBEMIND_DESKTOP_AUSFUEHREN=1` in der Umgebung des
   Werkzeug-Dienstes setzen. Ohne diese Angabe ist jede Aufgabe ein Trockenlauf. So lassen
   sich die Abnahmepunkte 2, 4 und 6 messen, ohne dass etwas klickt.
4. Den Pin von `spaces/desktop/Automation_ui` auf den Zweig `cc/space-desktop` versetzen und
   den Desktop-Dienst neu starten (Monitorliste, Bild je Monitor, `/api/desktop/task`,
   Tasten-Routen).
5. Abnahmepunkt 5: `brain-desktop` ins Agenten-Verzeichnis von Rowboat.

## Was aus dem heutigen Block entfällt (Vorschlag)

Der heutige Block führt 28 Ereignisse. Hinter keinem der Werkzeugnamen (`desktop_click`,
`desktop_type`, `desktop_task`, ...) steht Code. Vorschlag:

- `desktop.click`, `.type`, `.press_key`, `.scroll`, `.open_app`, `.task.create`, `.task.update`,
  `.task.list`, `.moire.scan`, `.moire.find`: streichen, ersetzt durch die sechs oben.
- `messaging.*` (3) und `openclaw.*` (11): gehören nicht in diesen Space. Nachrichten gehen
  nach außen und brauchen ein Freigabe-Tor wie bei Sales. Bis das entschieden ist: abschalten.
- `web.search`, `web.fetch`: gehören zum Recherche-Space (`fetch`).
