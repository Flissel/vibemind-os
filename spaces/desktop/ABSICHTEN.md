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

## Wie ein getippter Satz zur Aufgabe wird

Beispiel: "Öffne den Editor und schreibe Hallo."

1. Das Gehirn erkennt `desktop.task` (nur aus getippter Eingabe; gesperrt für autonome Pläne,
   Nachfasser und Hintergrundläufe).
2. Fester Aufruf `mcp:brain-desktop:spaces-desktop:desktop_task_start` mit `{task: "<Satz>"}`
   → liefert `id`. Ab hier steht die Aufgabe mit `zustand: laeuft` im Aufgabenbuch, und die
   App zeigt sie (Abnahmepunkt 6; gemessen unter 5 ms).
3. Auftrag `openfang:brain-desktop` mit `{task_id, task}`. Der Agent sieht hin, führt Schritte
   aus und schließt mit `desktop_task_finish`.
4. Stand während des Laufs: die App fragt `GET /api/desktop/task` am Desktop-Dienst ab (liest
   dasselbe Aufgabenbuch); das Gehirn kann `desktop_task_status(task_id)` als festen Aufruf
   nehmen.
5. Nachprüfung am Zustand, nicht am Text: `desktop_task_status(task_id)` → `zustand` ist
   `fertig`, jeder Schritt `ok: true`. Im Trockenlauf steht bei jedem Schritt `echt: false`
   und die Meldung beginnt mit "trocken"; der Agent schließt dann ehrlich mit
   `fehlgeschlagen`. Abnahmepunkt 3 gilt bis zu Felix' Schalter als "im Trockenlauf gemessen".

**Schritt 3 geht heute nicht.** Ein Agent in OpenFang kann seine Werkzeuge nicht selbst
aufrufen: Der Abo-Wrapper startet Claude mit `--safe-mode`, und das schaltet Werkzeug-Dienste
ab, auch eine ausdrücklich genannte Konfiguration (Beleg-Lauf 23.11., ein Lauf, 10 s, kein
Werkzeug-Aufruf). Die Erweiterung des Wrappers liegt fertig im Zweig `cc/wrapper-werkzeuge`
des äußeren Repos, wirkt aber erst, wenn für diesen einen Agenten `--safe-mode` ersetzt wird.
Das ist eine Entscheidung von Felix. Bis dahin gehen über das Gehirn nur die festen Aufrufe
(Schritte 2, 4, 5 und die lesenden Absichten).

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

1. **Werkzeug-Dienst in OpenFang eintragen** (für die festen Aufrufe des Gehirns):
   - Name: `spaces-desktop`, Transport stdio
   - Befehl: das Python des Betriebs, Argument `<vibemind-os>/spaces/desktop/mcp_server.py`
   - Umgebung, beide nötig, weil OpenFang Werkzeug-Dienste mit leerer Umgebung startet:
     - `DESKTOP_AUTOMATION_URL=http://127.0.0.1:8007`
     - `VIBEMIND_DESKTOP_AUFGABEN_DIR=<derselbe Ordner, den der Desktop-Dienst liest>`. Der
       Desktop-Dienst nimmt `%LOCALAPPDATA%\VibeMind\desktop-aufgaben`; fehlt die Angabe,
       antwortet jedes Aufgaben-Werkzeug mit `konfiguration: ...`.
   - Nicht setzen: `VIBEMIND_DESKTOP_AUSFUEHREN`. Ohne sie ist jede Aufgabe ein Trockenlauf.
2. **Block oben ins Verzeichnis** eintragen (mit `mcp_tools`) und `brain-desktop` neu
   erzeugen, Modellklasse `sonnet`. Heute hat der Agent nur `memory_store` und
   `memory_recall`.
3. **Pin von `spaces/desktop/Automation_ui` auf `6f6e271`** und Desktop-Dienst neu starten
   lassen (Monitorliste, Bild je Monitor, `/api/desktop/task`, Tasten-Routen,
   `/api/mcp/elements`, Shell-Routen aus). Der Push des Zweigs braucht Felix' Ja.
4. **Damit der Agent selbst Schritte ausführt:** Entscheidung zu `--safe-mode` (siehe oben),
   dann Zweig `cc/wrapper-werkzeuge` übernehmen. Die Werkzeugliste und der Dienst für den
   Wrapper stehen dort in `config/agent_tool_services.json`; sie muss dieselben neun Namen
   nennen wie `mcp_tools` hier.
5. **Echte Klicks** (Felix hat Ja gesagt, laut Koordination): erst wenn ein Lauf über OpenFang
   gezeigt hat, dass der Agent nach jedem Schritt neu hinsieht. Dann
   `VIBEMIND_DESKTOP_AUSFUEHREN=1` an beiden Stellen, an denen der Werkzeug-Dienst gestartet
   wird (OpenFang-Eintrag und `agent_tool_services.json`).
6. Abnahmepunkt 5: `brain-desktop` ins Agenten-Verzeichnis von Rowboat.

## Was aus dem heutigen Block entfällt (Vorschlag)

Der heutige Block führt 28 Ereignisse. Hinter keinem der Werkzeugnamen (`desktop_click`,
`desktop_type`, `desktop_task`, ...) steht Code. Vorschlag:

- `desktop.click`, `.type`, `.press_key`, `.scroll`, `.open_app`, `.task.create`, `.task.update`,
  `.task.list`, `.moire.scan`, `.moire.find`: streichen, ersetzt durch die sechs oben.
- `messaging.*` (3) und `openclaw.*` (11): gehören nicht in diesen Space. Nachrichten gehen
  nach außen und brauchen ein Freigabe-Tor wie bei Sales. Bis das entschieden ist: abschalten.
- `web.search`, `web.fetch`: gehören zum Recherche-Space (`fetch`).
