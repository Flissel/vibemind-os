# Desktop-Automatisierung: Bestand, Überarbeitung, Offenes

Stand: 23.11.2026, Desktop-Sitzung. Gemessen am laufenden Dienst nur lesend; es wurde
nichts geklickt und nichts getippt.

## 1. Was heute wirklich geht (gemessen)

| Was | Wie gemessen | Ergebnis |
|---|---|---|
| Desktop-Dienst läuft | `GET /api/health/health` | 200, `live_desktop` und `ocr` in Betrieb |
| Bild des Hauptmonitors | `GET /api/desktop/screenshot` | 200 in 0,04 s, JPEG mit rund 150 kB |
| Bildschirmtext | `GET /api/mcp/read-screen` | 200 in 0,5 bis 0,7 s; nur 140 bis 350 Zeichen für einen vollen Bildschirm (Tesseract) |
| Monitore | Feld `monitors` in `read-screen` | zwei Monitore, je 1920x1080 |
| Klick, Tippen, Scrollen im Dienst | im Code gelesen: `/api/mcp/click`, `/type`, `/scroll` → `handle_action` (pyautogui) | vorhanden, nicht ausgelöst |
| `brain-desktop` in OpenFang | `GET /api/agents` | läuft, Modell `claude-code/haiku` |

## 2. Was kaputt oder leer ist

1. **`brain-desktop` kann nichts tun.** Sein Manifest erlaubt nur `memory_store` und
   `memory_recall`. Im Verzeichnis fehlt `mcp_tools` für den Space; ohne den Eintrag bekommt
   ein Agent kein Werkzeug (Absicht).
2. **Die 28 Ereignisse des Space zeigen ins Leere.** Hinter `desktop_click`, `desktop_type`,
   `desktop_task`, `desktop_screenshot` usw. steht kein Code. Der Server `desktop-automation`
   ist in OpenFang nicht eingetragen (dort: `vibemind-db`, `laura`, `webapp-alerts`).
3. **Der Space-Ordner war leer.** Mit dem Commit "session snapshot" sind rund 600 Dateien
   unter `spaces/desktop/` entfallen (tools, agents, messaging, eyeterm, automation_ui_client).
   Übrig war nur das Submodul. Die App importiert daraus weiter; daher
   `No module named 'spaces.desktop.automation_ui_client'`. Der Katalog
   `data/space_capabilities/desktop.*` listet 54 Werkzeuge aus den entfallenen Dateien.
4. **Monitorliste fehlt:** `/api/desktop/monitors` → 404. Die App nimmt einen Monitor an.
5. **`/api/desktop/status` → 500** (eine gewöhnliche Methode wurde mit `await` aufgerufen).
6. **`/api/desktop/screen-info` war eine Attrappe:** fest 1920x1080, `monitors: []`.
7. **`/api/desktop/screenshot` beachtete `monitor` nicht**, meldete das JPEG als `png` mit
   Breite und Höhe 0 und lieferte höchstens ein neues Bild je Sekunde.
8. **Freie Aufgaben im Dienst brauchen einen eigenen Schlüssel.** `/api/llm/intent` (28
   Werkzeuge, bis 50 Runden) ruft OpenRouter mit `anthropic/claude-opus-4`. Die App startet
   den Dienst ohne Schlüssel, der Weg ist also tot, und er widerspricht "nur das Claude-Abo".
9. **`/api/automation/*`** (Klick-Weitergabe aus dem Bild der App) meldet `available: false`,
   Bildschirmgröße 0x0: Der Teil-Dienst wird nie gestartet.
10. **`/api/v1/workflow/*` sind Attrappen** (fester Fortschritt 0,5). Die Ereignis-Warteschlange
    der Werkzeug-Dienste hat keinen Abnehmer (im Code gesucht, nicht ausgeführt).

## 3. Was überarbeitet wurde

Im Space (`vibemind-os`, Zweig `cc/space-desktop`):

- **`mcp_server.py`**: neuer Werkzeug-Dienst `spaces-desktop` nach dem Muster des Ideen-Space.
  Kein Modell, kein Schlüssel, nur Standardbibliothek. Neun Werkzeuge: `desktop_status`,
  `desktop_screen_read`, `desktop_elements`, `desktop_find`, `desktop_screenshot`, `desktop_task_start`,
  `desktop_task_step`, `desktop_task_finish`, `desktop_task_status`.
- **`aufgaben.py`**: das Aufgabenbuch. Eine Datei je Aufgabe mit allen Schritten. Daraus
  liest die App, was geschieht, und der Prüfer, was wirklich ausgeführt wurde.
- **Sicherungen** (alle mit Test):
  - Schritte nur innerhalb einer laufenden Aufgabe; höchstens eine Aufgabe zugleich.
  - Ohne `VIBEMIND_DESKTOP_AUSFUEHREN=1` ist alles ein Trockenlauf: gebucht, nicht ausgeführt.
  - Datei `STOP` im Aufgaben-Ordner hält jeden Schritt und jede neue Aufgabe an.
  - Höchstens 40 Schritte und 10 Minuten je Aufgabe; danach ist die Aufgabe `abgebrochen`
    bzw. `abgelaufen`, mit Meldung im Ergebnis, und nimmt keinen Schritt mehr an.
  - Not-Aus, Laufzeit und Obergrenze werden vor jedem einzelnen Schritt geprüft.
  - Kein Schritt ohne Blick: Klicken, Tippen, Tasten und Scrollen lehnt das Werkzeug mit
    `nicht_hingesehen` ab, wenn der Agent seit dem letzten Schritt nicht `desktop_elements`,
    `desktop_screen_read` oder `desktop_find` aufgerufen hat. Ein fehlgeschlagener Blick,
    ein gespeichertes Bild und Warten zählen nicht.
  - Jeder Schritt steht vor der Ausführung im Aufgabenbuch (Art, Ort, Zeit); `echt` sagt,
    ob er wirklich an Maus oder Tastatur ging.
  - Nicht im Code absicherbar: dass eine Aufgabe von Felix getippt wurde. Der Werkzeug-Dienst
    sieht nur den Aufruf. Das muss dort gelten, wo die Aufgabe entsteht (Gehirn, Verzeichnis:
    `desktop.task` nur aus getippter Eingabe, nie aus Hintergrundläufen).
  - Die Adresse des Dienstes muss der eigene Rechner sein.
  - Kein Shell-Werkzeug, keine Dateien, kein Rechtsklick. Unbekannte Angaben werden abgelehnt.
  - Getippter Text steht nicht im Aufgabenbuch, nur seine Länge.
- **`automation_ui_client.py`**: schlanker Ersatz für das entfallene Modul (nur
  `is_available()`), damit der Import der App wieder gelingt.
- **`ANZEIGE.md`**, **`ABSICHTEN.md`**: Anzeige und Absichten.

Im Dienst (`Automation_ui`, öffentliches Repo, lokaler Zweig `cc/space-desktop`, nicht gepusht):

- `GET /api/desktop/monitors`: echte Monitore mit Lage und Größe, in der Form, die die App
  schon erwartet (`count`, `monitors[].index`, `.geometry`).
- `GET /api/desktop/screenshot?monitor=N`: liefert den verlangten Monitor als `jpeg` mit
  echter Breite und Höhe; unbekannter Monitor → 404; neues Bild alle 0,2 s statt 1 s.
- `GET /api/desktop/status`: antwortet wieder. `screen-info`: echte Werte.
- `GET /api/desktop/task`: die jüngste Aufgabe aus dem Aufgabenbuch, für die Anzeige.
- `POST /api/mcp/key`, `/api/mcp/hotkey`: Taste und Tastenkombination (fehlten in der Brücke).
- `GET /api/mcp/elements`: aktives Fenster und seine Bedienelemente mit Lage (lesend).
- **Shell-Routen abgeschaltet:** `/api/shell/*` wird nur noch eingehängt und `/api/mcp/shell`
  antwortet nur noch, wenn der Dienst mit `AUTOMATION_UI_ENABLE_SHELL=1` gestartet wird.
  Aufrufer über HTTP gibt es in vibemind-os, im Dienst-Repo und in der App keine (gesucht).

Tests: 24 im Space (`python -m pytest spaces/desktop/tests`), 12 im Dienst
(`cd backend && python -m pytest tests -o addopts=""`); alle grün. Die Tests des Dienstes
lesen den echten Bildschirm, bewegen aber nichts. Trockenprobe des Werkzeug-Dienstes gegen
den laufenden Dienst: `desktop_task_start` unter 1 ms, `desktop_screen_read` 0,5 s.

## 4. Stand der Abnahmepunkte

| # | Punkt | Stand |
|---|---|---|
| 1 | Agent und Absichten im Verzeichnis | geliefert (`ABSICHTEN.md`), Eintrag durch die Koordination offen |
| 2 | Satz landet bei `brain-desktop` | offen: Weg über das Gehirn gesperrt, Eintrag fehlt |
| 3 | Agent führt über OpenFang aus | offen: Werkzeug-Dienst in OpenFang eintragen; echte Ausführung nur mit Felix' Ja |
| 4 | App springt und zeigt | entfällt (Felix 23.11.): Der Space ist kein Ziel der Navigation mehr, siehe Abschnitt 10. Die Antwort trägt weiter `space: "desktop"` für die Standzeile; ob die App springt, regelt die Hülle. Im Space und im Gehirn baut nichts auf den Sprung. |
| 5 | Agent im Rowboat-Verzeichnis | offen, Koordination |
| 6 | nach 2 s sichtbar, dass die Aufgabe läuft | Baustein fertig (fester Aufruf unter 5 ms, Abfrage 1 mal je Sekunde); in der App noch nicht gemessen |
| 7 | aufgeschrieben, was angezeigt wird | `ANZEIGE.md` geschrieben; Vorlage bei Felix und Umsetzung in der App offen |

Kein Punkt ist in der laufenden App gemessen. Das Ziel der Sitzung ist damit nicht erreicht.

## 5. Was offen bleibt

1. **Wie gut der Agent sieht, ist nicht gemessen.** `desktop_screen_read` liefert nur
   OCR-Text ohne Koordinaten. Neu ist `desktop_elements` (Bedienelemente des aktiven Fensters
   mit Klickpunkt, über die Bedienhilfen von Windows). Das wirkt erst nach dem Ausrollen des
   Dienstes. Programme ohne Bedienhilfen (Spiele, manche Web-Inhalte) bleiben blind; ein Bild
   bekommt der Agent nicht zu sehen.
2. **Werkzeug-Schleife: ohne OpenFang gemessen, mit OpenFang nicht.** Siehe Abschnitt 6.
3. **Tippen überschreibt die Zwischenablage** des Nutzers (`handle_action` kopiert den Text
   und drückt Strg+V).
4. **Programme starten:** kein Werkzeug dafür im Space. `app_launch_or_focus` mit Positivliste
   gibt es in den Werkzeug-Diensten, aber nicht an der Brücke.
5. **Sicherheit des Dienstes:** Die Shell-Routen sind im Zweig abgeschaltet (Abschnitt 3),
   im laufenden Dienst bis zum Ausrollen aber noch offen. Der freie Aufgabenweg
   `/api/llm/intent` hat ein eigenes `shell_exec` und ist davon nicht berührt; er läuft ohne
   Schlüssel nicht.
6. **Alte Wege im Dienst:** `/api/llm/intent` (eigener Schlüssel), `/api/v1/workflow/*`
   (Attrappen), `/api/automation/*` (nie gestartet), `clawdbot`, `clawhub`. Entscheidung
   nötig: ausbauen oder stilllegen. Diese Sitzung hat sie nicht angefasst.
7. **Der Vertrag `spaces/_contract/desktop.contract.yaml`** nennt `desktop_skill` über
   `skill-coordinator`. Mit dem neuen Weg gehört er auf `brain-desktop` und die sechs
   Absichten umgeschrieben (Koordination, Bereich `_contract`).
8. **Katalog `data/space_capabilities/desktop.*`** ist veraltet und muss neu erzeugt werden.
9. **Die App** (voice-Repo) importiert weiter entfallene Module (`spaces.desktop.eyeterm`,
   `.messaging`, `.tools`, `.agents`). Nur `automation_ui_client` ist ersetzt. Für die Hülle.
10. **Feste Pfade** auf den Betriebs-Checkout im Dienst (`_SKILL_LIB_ROOT`, `capture_mon*.py`).

## 6. Messung: Werkzeuge in einer Schleife (Trockenlauf, 23.11.)

Ein Lauf mit `claude -p --model haiku`, nur mit dem Werkzeug-Dienst `spaces-desktop`, ohne
`VIBEMIND_DESKTOP_AUSFUEHREN`; über den Budget-Wächter auf `brain-desktop` gebucht. Aufgabe:
zweimal scrollen, dann sagen, was oben steht. Es wurde nichts ausgeführt.

| Zeit | Aufruf |
|---|---|
| 5,6 s | `desktop_task_start` |
| 6,6 s | `desktop_screen_read` |
| 9,1 s | `desktop_task_step` (scroll) |
| 10,9 s | `desktop_task_step` (scroll) |
| 12,6 s | `desktop_task_finish` (`fehlgeschlagen`) |
| 18,3 s | Ende, 7 Züge |

Was das zeigt:

- Haiku ruft fünf Werkzeuge nacheinander in einem Lauf auf, rund 1,5 s je Aufruf.
- Das Aufgabenbuch stimmt mit den Aufrufen überein: zwei Schritte, beide als "trocken" gebucht.
- Der Agent hat mit `fehlgeschlagen` abgeschlossen, weil die Schritte nicht ausgeführt
  wurden. Im Trockenlauf ist das die ehrliche Meldung; die Prüferin sollte es so erwarten.
- Er hat zwischen den Schritten nicht wieder hingesehen, obwohl die Anweisung das verlangt.
  Seitdem setzt das Werkzeug es durch (Abschnitt 3); der zweite Schritt dieser Messung würde
  heute abgelehnt. Mit einem Modell ist das noch nicht neu gemessen.
- Bis zum ersten Werkzeug vergehen 5,6 s. Die 2 s aus Abnahmepunkt 6 hält nur der feste
  Aufruf `desktop_task_start` durch das Gehirn, nicht der Agent.

Nicht gemessen: derselbe Lauf über OpenFang (dort hat `brain-desktop` die Werkzeuge noch nicht).

## 7. Importe der App, die ins Leere gehen

`python/spaces` der App zeigt auf `spaces/` in vibemind-os. Dort fehlen diese Module
(Stand voice `7ed50d5`; nichts davon wurde wiederhergestellt):

| Modul | importiert in |
|---|---|
| `spaces.desktop.eyeterm.*` (app, config, headless, state, audio, cursor, routing, vision) | `electron_backend.py`, `ipc/eyeterm_handlers.py`, 9 Testdateien |
| `spaces.desktop.messaging.*` (relevance_filter, incoming_handler, messaging_pipeline) | `ipc/voice_manager.py`, `swarm/orchestrator/tool_registry.py` |
| `spaces.desktop.tools.desktop_tools`, `.tools.moire_tools`, `.adapted.desktop_tools` | `swarm/orchestrator/tool_orchestrator.py`, `tool_registry.py`, `tools/moire_tools.py`, Tests |
| `spaces.desktop.agents.desktop_agent` | `swarm/backend_agents/__init__.py`, `tool_registry.py` |
| `spaces.desktop.broadcast.desktop_broadcast_agent` | `swarm/orchestrator/intent_orchestrator.py` |
| `spaces.desktop_space.hand_motion` | `scripts/hand_tracking_server.py`, ein Test |

Vorhanden sind `spaces.desktop.automation_ui_client` (ersetzt) und
`spaces.desktop.Automation_ui.backend.app.services.clawdbot_bridge` (im Submodul).
Insgesamt 9 Quelldateien und 14 Testdateien der App importieren aus `spaces.desktop`.

## 8. Der Agent und seine Werkzeuge über den Abo-Wrapper (23.11., nach dem Neustart)

Ein Agent in OpenFang kann seine Werkzeuge nicht selbst aufrufen: Der Abo-Wrapper startet
Claude mit `--safe-mode`, und das schaltet Werkzeug-Dienste ab. Im äußeren Repo liegt im Zweig
`cc/wrapper-werkzeuge` eine Erweiterung (Vorgabe aus, ein Dienst für einen Agenten aus einer
festen Beschreibung, Werkzeugliste abschließend). Zwei Läufe aus dem Arbeitsordner, beide im
Trockenlauf, beide auf `brain-desktop` gebucht:

| Lauf | Aufbau | Ergebnis |
|---|---|---|
| 1 | mit `--safe-mode` | 10,0 s, kein Werkzeug-Aufruf; das Modell meldet, die Werkzeuge fehlen |
| 2 | Messschalter, ohne `--safe-mode`, Wegwerf-Verzeichnis, `--tools ""` | 12,9 s gesamt, davon 8,2 s Claude; fünf Aufrufe in sechs Zügen; Aufgabenbuch: zwei Schritte, `echt: false` |

Was in Lauf 2 geladen war (Startereignis von Claude, nur Namen):

- Werkzeuge: genau die neun von `spaces-desktop`, kein eingebautes.
- Werkzeug-Dienste: nur `spaces-desktop`. Befehle: keine. Skills: keine.
- Plugins: vier eingebaute (`cc-plugin-agents-md`, `-telemetry`, `-privacy`,
  `-plugin-authoring`), keines der vom Nutzer installierten.
- Agenten-Namen: sechs eingebaute; ohne Werkzeug zum Starten nicht benutzbar.
- Hooks: kein Hook-Ereignis in der Ausgabe.
- Nutzer-`CLAUDE.md`: nicht bewiesen. Die erste Anfrage hatte 2.868 Tokens für System-Text
  und neun Werkzeug-Beschreibungen; die Datei allein hat rund 3 kB. Das spricht dagegen,
  dass sie geladen wurde, ist aber eine Schätzung.

Ob `--safe-mode` für diesen einen Agenten ersetzt wird, entscheidet Felix. In den Betrieb
ist nichts davon gegangen.

## 9. Weg 2: Das Gehirn führt die Schleife (23.11., Entscheidung von Felix)

Der Agent bekommt keine Werkzeuge. Je Runde liest das Gehirn den Bildschirm, fragt
`brain-desktop` nach genau einer Aktion, prüft sie gegen einen Vertrag und führt sie als
festen Aufruf aus. Dateien: `brain/the_brain/core/desktop_schleife.py`,
`desktop_schleife_contract.py`, elf Zeilen in `plan_executor.py`. Im Werkzeug-Dienst neu:
die Aktion `note` (eine Zeile ins Aufgabenbuch, am Bildschirm geschieht nichts).

Getestet mit einem Agenten als Attrappe gegen den echten Werkzeug-Dienst: 47 Tests in
`brain/the_brain/tests/test_desktop_schleife.py`, dazu unverändert 26 im Space.

Nicht gemessen:

- Ein Lauf mit echtem Agenten. Ob Haiku oder Sonnet zuverlässig ein einzelnes JSON-Objekt
  liefert und wie oft der Vertrag einen sinnvollen Vorschlag ablehnt, ist offen.
- Der Planausführer als Ganzes mit dem Eingriff. Die große Testreihe des Gehirns lief nicht
  (Speicher bei 70,6, über der Schwelle); geprüft ist nur, dass die Datei gelesen wird und
  die Schleife für sich läuft.
- Die Dauer: ein Agentenlauf je Runde, also grob 7 bis 10 s je Schritt und bis zu acht
  Budget-Läufe je Aufgabe.
- Die Schleife läuft im Hop des Planausführers bis zu 8 Minuten. Ob das Gehirn so lange auf
  einen Hop warten darf oder die Schleife in den Hintergrund gehört, entscheidet die
  Koordination.

Der Zweig `cc/wrapper-werkzeuge` im äußeren Repo (Weg 1) bleibt liegen und geht nicht in den
Betrieb.

## 10. Der Space als Augen (23.11., Vorgabe von Felix)

Der Desktop-Space arbeitet im Hintergrund mit. Er ist kein Ziel der Navigation. Er liest,
in welchem Programm und Fenster wir sind, und bestätigt nach einem Schritt eines anderen
Space, dass etwas wirklich passiert ist. Später kann er beim Auswählen klicken. Quelle fürs
Mitsehen sind Programm, Fenstertitel und Bedienelemente; Texterkennung nur auf Zuruf.

### Stufe 1, gebaut (nur lesend)

**Werkzeug `desktop_blick`** (`mcp_server.py`, `blick.py`). Liefert `programm`
(Programmdatei ohne `.exe`), `fenstertitel` (höchstens 120 Zeichen), `monitor` (1-basiert,
Reihenfolge wie `desktop_screenshot`), `elemente` (höchstens 20, je `name` und `art`, ohne
Klickpunkt), `elemente_gekuerzt`, `elemente_lesbar`, `zeitpunkt` (UTC) und `dauer_ms`.
Programm, Titel und Monitor liest der Werkzeug-Dienst selbst über user32/kernel32; die
Elemente kommen von `GET /api/mcp/elements`. Ein Blick zählt nicht als Hinsehen für einen
Schritt der Schleife.

**Messung am laufenden Dienst (23.11., Trockenlauf, sechs Läufe):** 4 bis 23 ms je Blick.
Nur das Fenster lokal: 0,2 bis 0,9 ms. Zum Vergleich: `desktop_screen_read` 2,3 s.

**Befund damals:** Dem Dienst fehlte `uiautomation`, er lieferte keine Elemente. Seit dem
23.11. abends ist `uiautomation` 2.0.29 im Betriebs-venv.

**Messung mit Bedienelementen (23.11. abends, Trockenlauf, Dienst selbst gestartet und wieder
beendet, nur eigene Testfenster).** Dauer je Aufruf in ms, drei Läufe je Fenster; Zahl der
Elemente; ob gesperrt. Fenstertitel stehen hier bewusst nicht.

| Fenster | `desktop_blick` | `desktop_elements` | Elemente (mit Namen) | gesperrt |
|---|---|---|---|---|
| Editor, eigene Datei | 597 / 79 / 64 (erster Lauf kalt) | 60 bis 62 | 34 (34) | nein |
| Explorer, eigener Ordner | 115 bis 197 | 118 bis 172 | 52 (41) | nein |
| Edge, eigene neutrale Seite (Wegwerf-Profil) | 102 bis 136 | 115 bis 126 | 21 | ja, fälschlich (s. u.) |
| Edge, Seite mit Titel "Anmelden" | 0 (am Titel, vor jeder Anfrage) | nicht gefragt | – | ja |
| Edge, Seite mit beschriftetem Passwortfeld | – | – | – | ja |
| Edge, InPrivate | – | – | – | ja (Kennzeichen im Titel) |
| VibeMind-App | rund 40 | 23 | 1 (0) | nein |

- **Qualität der Namen:** Editor und Explorer: brauchbar (Menü, Reiter, Listeneinträge).
  Edge: bei 60 Elementen nur Symbolleiste (55 von 60 sind Knöpfe), die Seite selbst erst ab
  rund 100 (bei 200 erlaubten: 102 Elemente, 301 Knoten, 263 ms statt 212 ms). Deshalb prüft
  der Blick jetzt 200 Elemente auf die Sperrliste (Obergrenze des Dienstes); hinaus gehen
  weiter höchstens 20, und das sind in Edge Symbolleistenknöpfe.
- **Die VibeMind-App** (Electron) zeigt der Bedienhilfe kein Innenleben: ein Element, ohne
  Namen. Ein Space-Wechsel ist über Elemente nicht zu sehen; der Fenstertitel ist immer
  derselbe. Sichtbar wird er nur über Texterkennung oder über die App selbst.
- **Fehlsperre in Edge, behoben (0ab9de9b):** Jede Edge-Symbolleiste hat einen Knopf mit
  "Kartennummer" im Namen. Zahlungswörter (Kartennummer, IBAN, CVC, Kreditkarte, Prüfziffer)
  zählen seitdem nur an Eingabefeldern. Nach der Reparatur NICHT neu gemessen (Speicher
  reichte nicht); belegt nur über die gespeicherten Elementlisten der Messung und einen Test
  mit den gemessenen Namen: neutrale Seite nicht gesperrt, beschriftetes Passwortfeld
  gesperrt (es steht bei 200 Elementen drin, bei 60 nicht).

**Sperrliste** (fest in `blick.py`, getestet in `tests/test_blick.py`). Ein gesperrtes
Fenster liefert nur `gesperrt: true`, `zeitpunkt` und `dauer_ms`: kein Titel, kein Programm,
keine Elemente, auch kein Grund. Erkannt wird an drei Stellen:

1. **Programmdatei** (beginnt mit): Passwort-Manager (KeePass/KeePassXC, 1Password,
   Bitwarden, Dashlane, LastPass, Enpass, RoboForm, Keeper, NordPass, Proton Pass, Password
   Safe), Banking-Programme (StarMoney, Programme mit "banking" im Namen, Finanzmanager,
   MoneyMoney, Hibiscus, Subsembly, Outbank), Windows-Anmeldung und -Kennwortabfrage
   (`credentialuibroker`, `consent` = Benutzerkontensteuerung, `logonui`, `lockapp`, `credwiz`).
2. **Fenstertitel** (enthält): Kennzeichen privater Fenster (InPrivate, Inkognito/Incognito,
   Privates Surfen, Private Browsing, privates Fenster), Namen von Passwort-Managern und
   "Passwort/Password/Kennwort", Banken und Zahlungsdienste (Sparkasse, Volksbank, Postbank,
   comdirect, DKB, N26, PayPal, Klarna, ...), Banking-Wörter (Banking, Überweisung,
   Kontostand, pushTAN, SEPA), Anmeldung (Anmelden, Login, Sign in, Zwei-Faktor,
   Bestätigungscode, Windows-Sicherheit).
3. **Bedienelemente** (Name, Automation-Id, Klasse): Passwort, Kennwort, Passphrase,
   Sicherheits- und Einmalcode an jedem Element; an Eingabefeldern (Edit, ComboBox)
   zusätzlich Kartennummer, IBAN, CVC und die Wörter PIN, TAN, OTP, PWD. Ein solches Element
   sperrt das ganze Fenster.

Geprüft wird am Fenster, BEVOR der Desktop-Dienst nach Elementen gefragt wird, und noch
einmal an dem Fenster, das der Dienst tatsächlich gelesen hat (falls es inzwischen
gewechselt hat). Im Zweifel (Fehler in der Prüfung) gilt gesperrt.

Was durchrutschen kann:

- **Chrome-Inkognito:** Chrome schreibt "Inkognito" nicht in den Fenstertitel. Ein
  Inkognito-Fenster mit einer Seite ohne verräterischen Titel wird gezeigt. Abhilfe nur über
  die Bedienhilfen des Fensters oder den Fensterstil; nicht gebaut.
- **Webseiten mit neutralem Titel:** Banken oder Kennwortseiten, deren Titel keinen der
  Teile enthält, erkennt nur die Elementprüfung, und die erreicht die Seite in Edge erst
  hinter rund 100 Symbolleisten-Elementen (gemessen).
- **Unbenannte Anmeldefelder:** Ein Kennwortfeld ohne Namen und ohne passende Kennung kam an
  der eigenen Testseite mit leerem Namen an. Abhilfe gebaut: `is_password` aus UIA
  `IsPassword` (siehe unten); wirkt erst, wenn der Dienst neu ausgerollt ist. Nicht gemessen.
- **Nur die ersten 200 Elemente** werden geprüft (Obergrenze des Dienstes); ein Anmeldefeld
  weiter hinten im Baum rutscht durch.
- **Unbekannte Programme:** nur die genannten Passwort-Manager und Banking-Programme.
- **Andere sichtbare Fenster:** Der Blick zeigt nur das aktive Fenster; was daneben offen
  ist, prüft er nicht, zeigt es aber auch nicht.
- **Elementnamen sind Inhalt:** Listeneinträge können Betreffzeilen, Dateinamen oder Namen
  tragen. Deshalb höchstens 20 Namen, je 60 Zeichen.

**Nachprüfung `truth:desktop_sicht`** (`brain/the_brain/core/desktop_sicht.py`, Zweige in
`capability_validator.py` und `world_observer.py`). Eine Fähigkeit eines beliebigen Space
schreibt in `capabilities.yaml`:

```yaml
validator:
  kind: truth:desktop_sicht
  on_fail: report
  postcondition:
    programm: "^(chrome|msedge)$"     # Muster auf die Programmdatei
    fenstertitel_enthaelt: "{arg}"    # {arg} = Argument des Schritts
    element_enthaelt: "Speichern"
```

VERIFIED, wenn alles zu sehen ist; REFUTED, wenn Lesbares nicht passt; UNVERIFIED bei
gesperrtem Fenster, unlesbaren oder abgeschnittenen Elementen, fehlenden Plan-Angaben oder
wartender Freigabe. Der Grund ist ein Satz: "Zu sehen: Fenster „…“ in Programm …". Das
Signal enthält nur Wahrheitswerte. Gelesen wird über `desktop_blick` mit eigenem
Laufzeit-Kontext (Ereignis `desktop.blick` im Verzeichnis, eingetragen); die Plan-Angaben
reicht der Plan-Ausführer unter `_laufzeit` an den Validator weiter, die Fähigkeit selbst
bleibt unverändert. Noch benutzt keine Fähigkeit den Prüfer.

**Erster Nutzer: kein geeigneter gefunden (23.11. abends).** Gesucht: eine Fähigkeit eines
anderen Space, deren Wirkung am Bildschirm sichtbar ist und die heute läuft. Der beste
Kandidat ist `browser_automation` (`openfang:openclaw-visible`, steuert ein sichtbares
Chrome). Sie läuft heute nicht: Der openclaw-Gateway (Port 18793) lauscht nicht. Und auch wenn
er läuft, liest der Blick das Vordergrundfenster: Arbeitet Felix daneben in einem anderen
Programm, wäre "nicht Chrome" eine falsche Widerlegung. Die VibeMind-App selbst (Space-Wechsel)
zeigt keine Bedienelemente und immer denselben Titel. Vorschlag für den ersten Nutzer, sobald
openclaw-visible läuft: an `browser_automation`

```yaml
validator:
  kind: truth:desktop_sicht
  on_fail: report
  postcondition:
    programm: "^chrome$"
```

**Regel (Koordination, 23.11.): Der Prüfer gilt nur, wenn der Schritt selbst das Fenster in
den Vordergrund gebracht hat; sonst UNVERIFIED, nie REFUTED.** Der Blick liest das
Vordergrundfenster, und Felix arbeitet oft daneben: Ein anderes Programm vorn sagt nichts über
den Schritt. Noch nicht gebaut. Bauvorschlag: Die Fähigkeit nennt das Programm, das sie nach
vorn holt (das `programm`-Muster), und der Prüfer sagt REFUTED nur, wenn der Blick genau dieses
Programm vorn sieht und Titel oder Element nicht passen. Steht ein anderes Programm vorn, heißt
das UNVERIFIED ("nicht im Vordergrund"). Ein `programm`-Muster allein kann dann nie widerlegen,
nur bestätigen.

### Vorschlag, noch nicht gebaut

**(a) Blick als Kontext für einen Schritt eines anderen Space, ohne dass er gespeichert wird.**

- Die Fähigkeit erklärt es selbst: `sicht: vorher` in `capabilities.yaml`. Nur dann liest das
  Gehirn direkt vor dem Schritt einen Blick (fester Aufruf, eigener Laufzeit-Kontext wie oben).
- Der Blick lebt nur im Speicher des Hops, als lokale Variable in `_exec_hop_kern`, nicht in
  `plan_ctx`, `state` oder `HopResult`. An den Schritt geht er als internes Argument `_sicht`;
  Angaben mit Unterstrich erreichen einen Werkzeug-Dienst nie (das prüft der Test mit dem
  nachgebauten OpenFang schon heute). Bei einem Agenten-Schritt wird er als gekennzeichneter
  Block an die Nachricht gehängt ("SICHT, nur für diesen Schritt, nicht speichern").
- Was das Gehirn darüber festhält, ist nur eine Zählzeile: gelesen ja/nein, gesperrt ja/nein,
  Anzahl Elemente, Dauer. Das gilt für Logger, `plan_started` und andere Bus-Ereignisse, den
  Plan-Recorder, `decision_outcome`, Erfahrungs- und Lernspeicher.
- **Widerspruch, offen:** Der Grund-Satz von `truth:desktop_sicht` enthält heute den
  Fenstertitel, wie verlangt. Das Urteil des Validators landet im `HopResult` und damit im
  Plan-Recorder. Vorschlag: Der Satz geht nur in die Antwort an den Chat; gespeichert wird
  "VERIFIED (programm, fenstertitel, element)". Entscheidung bei der Koordination.

**(b) Dauerndes Mitlesen oder ein Blick je Schritt?** Ein Blick je Schritt reicht. Kosten
heute: 4 bis 25 ms lokal, dazu je Blick eine Zulassung und eine Kostenreservierung bei
OpenFang (zwei Anfragen, ein Eintrag je Blick). Bei zwei Blicken je Schritt (vorher als
Kontext, nachher als Nachprüfung) ist das gegen die Laufzeit des Schritts selbst
vernachlässigbar. Dauerndes Mitlesen ginge im Desktop-Dienst über das Windows-Ereignis
"Vordergrundfenster gewechselt" (`SetWinEventHook`, `EVENT_SYSTEM_FOREGROUND`) fast
kostenlos, aber: Der Dienst muss dafür neu ausgerollt werden, es braucht einen Kanal zum
Gehirn oder zur App, und ein Strom von Fenstertiteln ist ein Tätigkeitsprotokoll des
Nutzers. Empfehlung: kein Takt und kein Dauerstrom. Erst wenn die Standzeile das aktive
Programm zeigen soll, ein Ereignis im Dienst, mit derselben Sperrliste und ohne Verlauf (nur
der letzte Stand).

**(c) "Wähl den zweiten Eintrag aus": gebaut (a4c7081a), nur mit Attrappen geprüft.**
Abweichend vom Vorschlag unten: MenuItem gehört NICHT zu den Auswahl-Arten (ein Menüeintrag
führt einen Befehl aus). Der Agent nennt den Eintrag (`{"aktion":"auswahl","ziel":...}`), das
Gehirn zählt in Lesereihenfolge nach (Zeile für Zeile, Zeile = unter 8 Pixel
Höhenunterschied, darin von links); passt der Name nicht zur Stelle, wird abgelehnt.
Ordnungswörter: erste bis zehnte, "2.", letzte, englisch first bis fifth und last. Die Art
nennt der Satz, nicht der Agent: ein Nomen höchstens drei Wörter hinter dem Ordnungswort
(Eintrag, Datei, Ordner, Zeile, Element: Liste/Baum/Tabelle; Reiter, Tab: Reiter). Keine oder
zwei verschiedene Angaben heißt nachfragen (abgelehnt, `auswahl_unklar`). Der Satz muss ein
Auswählen verlangen (wählen, markieren, klicken, nehmen, select; NICHT öffnen). Eine Auswahl
ist kein Vorlauf für Enter oder Leertaste (Befund der Sicherheitsprüfung). Nachprüfung in der
nächsten Runde, bevor der Agent wieder gefragt wird: `ausgewaehlt` falsch heißt Abbruch
(`auswahl_nicht_bestaetigt`), fehlt der Zustand, wird "nicht prüfbar" vermerkt. Den Zustand
liefert `desktop_elements` nur, wenn der Dienst `is_selected` schickt. Diese Zeile im Dienst
(`screen_description.py`, SelectionItemPattern) ist im Submodul `Automation_ui` als lokaler
Commit angelegt, aber noch nicht gepinnt und nicht ausgerollt. Bis sie im laufenden Dienst ist,
endet jede echte Auswahl mit "nicht prüfbar". Grenze: gezählt wird nur unter den höchstens 40
Elementen, die die Schleife sieht ("letzte" heißt: der letzte davon). Ein Ordnungswort zählt
nur, wenn zwischen ihm und dem Nomen kein Artikel, Pronomen oder Verhältniswort steht ("achte
auf den Eintrag" nennt keine Stelle).

**Werkzeugaufrufe des Agenten in der Schleife.** brain-desktop behält in `agent.toml` die
Werkzeuge der Schleife, weil OpenFang feste Aufrufe gegen die Werkzeugliste des Agenten prüft,
in dessen Namen gerufen wird. Ob ein Werkzeug VOR seiner Ausführung abgefangen werden kann,
entscheidet sich nur in OpenFang: `crates/openfang-runtime/src/tool_runner.rs`, `execute_tool`,
Zeile 136 (Werkzeugliste, `allowed_tools`) und Zeile 177 (Freigabe, `requires_approval`). Dort
fehlt eine Werkzeugliste je Anfrage: Eine leere Liste für den Agentenlauf der Schleife würde
jeden Aufruf vor der Ausführung ablehnen. Im Gehirn ist die früheste Stelle das Stream-Ereignis
`tool_use` in `capability_targets.py` (`_call_streaming`). Dort bricht der Agentenaufruf der
Schleife jetzt ab (`OpenFangExecutor(..., nur_text=True)`), die Schleife endet mit
`agent_hat_werkzeug_benutzt`. Was OpenFang bis dahin ausgeführt hat, ist geschehen. Vorher gab
es eine Lücke: Riss der Stream ab, fiel der Ausführer auf `/message` zurück, und dort ist
`tool_calls` immer leer. Für brain-desktop gibt es diesen Rückfall nicht mehr. Ein Test zeigt
den schlimmsten Fall (OpenFang führt einen Klick des Agenten aus, bevor das Gehirn ihn sieht):
Der Trockenlauf-Schalter hält, der Schritt ist nur gebucht (`echt: false`), der Desktop-Dienst
bekommt nichts.

**Unbeschriftete Kennwortfelder.** `uiautomation` (2.0.29) liefert die Eigenschaft
`IsPassword`. Der Dienst gibt sie als `is_password` aus (Submodul-Commit wie oben), und die
Sperrliste sperrt danach, auch ohne Namen. Am echten Fenster erst wieder messen, wenn Stand und
Grenze mindestens 7 GB Abstand zeigen.

Der ursprüngliche Vorschlag:

1. Blick mit Klickpunkten (nur intern im Gehirn, nicht in der Antwort): die Elemente der
   Arten ListItem, TreeItem, TabItem, MenuItem, DataItem.
2. Auflösen: Ordnungswort ("ersten" bis "zehnten", "letzten") aus dem Satz; Einträge
   derselben Art, die in einer Spalte oder Zeile liegen, nach `y`, dann `x` sortieren. Gibt es
   mehrere solche Listen, wird nachgefragt statt geraten.
3. Vertrag: eine neue Aktion `auswahl` in `desktop_schleife_contract.py`. Erlaubt ist nur ein
   Klick, keine Taste, und nur auf ein Element, das im Blick genau so steht, aktiv ist und zu
   den Auswahl-Arten gehört. Heikle Stämme (löschen, senden, kaufen, ...) gelten wie bisher:
   Ein Eintrag "Konto löschen" wird nur ausgewählt, wenn der Satz es wörtlich verlangt.
4. Ausführen als Schritt im Aufgabenbuch (`desktop_task_step`), damit die App es zeigt und der
   Not-Aus greift; Trockenlauf bleibt Vorgabe, echt nur mit Felix' Ja.
5. Nachprüfen mit einem zweiten Blick: Der Eintrag ist ausgewählt. Dafür muss der
   Desktop-Dienst den Auswahlzustand liefern (Muster "SelectionItem", heute nicht in der
   Antwort); bis dahin ersatzweise ein geänderter Fenstertitel, sonst UNVERIFIED.
