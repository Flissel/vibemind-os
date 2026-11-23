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
  Kein Modell, kein Schlüssel, nur Standardbibliothek. Acht Werkzeuge: `desktop_status`,
  `desktop_screen_read`, `desktop_find`, `desktop_screenshot`, `desktop_task_start`,
  `desktop_task_step`, `desktop_task_finish`, `desktop_task_status`.
- **`aufgaben.py`**: das Aufgabenbuch. Eine Datei je Aufgabe mit allen Schritten. Daraus
  liest die App, was geschieht, und der Prüfer, was wirklich ausgeführt wurde.
- **Sicherungen** (alle mit Test):
  - Schritte nur innerhalb einer laufenden Aufgabe; höchstens eine Aufgabe zugleich.
  - Ohne `VIBEMIND_DESKTOP_AUSFUEHREN=1` ist alles ein Trockenlauf: gebucht, nicht ausgeführt.
  - Datei `STOP` im Aufgaben-Ordner hält jeden Schritt und jede neue Aufgabe an.
  - Höchstens 40 Schritte und 10 Minuten je Aufgabe.
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

Tests: 16 im Space (`python -m pytest spaces/desktop/tests`), 8 im Dienst
(`cd backend && python -m pytest tests -o addopts=""`); alle grün. Die Tests des Dienstes
lesen den echten Bildschirm, bewegen aber nichts. Trockenprobe des Werkzeug-Dienstes gegen
den laufenden Dienst: `desktop_task_start` unter 1 ms, `desktop_screen_read` 0,5 s.

## 4. Stand der Abnahmepunkte

| # | Punkt | Stand |
|---|---|---|
| 1 | Agent und Absichten im Verzeichnis | geliefert (`ABSICHTEN.md`), Eintrag durch die Koordination offen |
| 2 | Satz landet bei `brain-desktop` | offen: Weg über das Gehirn gesperrt, Eintrag fehlt |
| 3 | Agent führt über OpenFang aus | offen: Werkzeug-Dienst in OpenFang eintragen; echte Ausführung nur mit Felix' Ja |
| 4 | App springt und zeigt | offen: Hülle baut nach `ANZEIGE.md`; Dienst muss ausgerollt sein |
| 5 | Agent im Rowboat-Verzeichnis | offen, Koordination |
| 6 | nach 2 s sichtbar, dass die Aufgabe läuft | Baustein fertig (fester Aufruf unter 5 ms, Abfrage 1 mal je Sekunde); in der App noch nicht gemessen |
| 7 | aufgeschrieben, was angezeigt wird | `ANZEIGE.md` geschrieben; Vorlage bei Felix und Umsetzung in der App offen |

Kein Punkt ist in der laufenden App gemessen. Das Ziel der Sitzung ist damit nicht erreicht.

## 5. Was offen bleibt

1. **Der Agent sieht schlecht.** `desktop_screen_read` liefert nur OCR-Text ohne Koordinaten.
   Die Werkzeug-Dienste können mehr (Bedienelemente mit Lage: `handoff_list_actionable`,
   `handoff_describe_screen`), das hängt aber nicht an der Brücke des Dienstes. Nächster
   Schritt: eine lesende Route dafür und ein Werkzeug `desktop_elements`.
2. **Nicht geprüft: ob ein Agent mit dem Abo-Modell in OpenFang Werkzeuge in einer Schleife
   aufruft** (sehen, Schritt, sehen). Davon hängt Abnahmepunkt 3 ab.
3. **Tippen überschreibt die Zwischenablage** des Nutzers (`handle_action` kopiert den Text
   und drückt Strg+V).
4. **Programme starten:** kein Werkzeug dafür im Space. `app_launch_or_focus` mit Positivliste
   gibt es in den Werkzeug-Diensten, aber nicht an der Brücke.
5. **Sicherheit des Dienstes:** `/api/shell/*` und `/api/mcp/shell` führen Befehle ohne
   Anmeldung aus. Der Dienst lauscht nur am eigenen Rechner, jeder lokale Prozess erreicht
   sie aber. Vorschlag: diese Routen in der von der App gestarteten Fassung abschalten.
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
