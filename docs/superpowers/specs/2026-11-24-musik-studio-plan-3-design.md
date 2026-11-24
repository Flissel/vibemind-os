# Musik-Studio Plan 3: Musik-Claw, Bestellen im Chat, Musik automatisch in Laura

**Datum:** 2026-11-24
**Status:** Entwurf, wartet auf Felix' Freigabe
**Baut auf:** `2026-11-23-musik-studio-design.md` (Teil A), Plan 1 (Sets), Plan 2 (Videomusik,
`spaces/musik/docs/LAURA-VERTRAG.md`)
**Nächster Schritt:** Umsetzungsplan (writing-plans)

## 1. Wozu

Felix am 23.11.: Laura, der Video-Space, soll einbezogen werden, „damit wir automatisch Musik
unterlegen können". Am 24.11. hat er entschieden: Musik kommt auf **beiden** Wegen unter ein
Laura-Video, über den Video-Claw von selbst und auf Zuruf im Chat. Den Chat bedient ein
**eigener Musik-Claw** (Weg B), dazu kommt das Chatfenster der App (Weg A; Felix: „und a").

Damit schließt Plan 3 auch die offene Abnahme von Teil A: Schritt 5 der Spec, „beide Aufträge
wurden über den Musik-Claw im Chat bestellt, nicht per Kommandozeile".

## 2. Ziele

1. **Video-Claw:** Jedes Reel bekommt beim Bauen Musik, die genau so lang ist wie das Reel,
   unter der Sprecherstimme liegt und mitgerendert wird. Ausnahme: der Auftrag sagt „ohne Musik".
2. **Musik-Claw über Telegram:** Felix schreibt einem eigenen Bot, was er braucht (ein Set,
   Musik für 30 s, Musik unter Laura-Video X). Der Claw bestellt, meldet den Fortschritt und
   schickt die fertige MP3 bzw. das Video in den Chat.
3. **Chatfenster der App:** Derselbe Wunsch in der VibeMind-App landet über das Gehirn beim
   Musik-Dienst (Space „musik" im Register).
4. **Auf Zuruf in Laura:** Felix nennt ein Laura-Video beim Namen. Der Musik-Claw findet die
   Zeitleiste, bestellt Musik passender Länge, legt sie als Audio-Clip hinein, lässt Laura
   rendern und schickt das fertige Video in den Chat (Felix am 24.11.: „Ja, mit Rückversand").

## 3. Nicht-Ziele

- Lauras Code ändern. Plan 3 nutzt nur Lauras vorhandene API.
- Mehrere Songs für lange Videos, Zeitpunkte im Video (Höhepunkt auf Sekunde X).
- Teil B (Live-Pult), Suno-Downloads, eine eigene Musik-Oberfläche in der App.

## 4. Anti-Ziele (wäre ein Fehlschlag, auch wenn es technisch läuft)

- Die Musik übertönt den Sprecher im Reel.
- Ein Reel ist durch die Musik länger oder kürzer als ohne, oder die Musik hört mitten im
  Takt auf, weil Laura das Reel nachträglich kürzt.
- In Laura werden vorhandene Clips gelöscht oder ersetzt, oder es wird an einer Zeitleiste
  gearbeitet, die niemand genannt hat.
- Eine WAV ist ohne Signatur oder dauerhaft aus dem Netz abrufbar.
- Der Agent bekommt ein Shell- oder Dateiwerkzeug, oder ein fremder Song kommt in den Bestand.
- Ein stiller Abbruch: ein Auftrag, der weder fertig noch gescheitert meldet.

## 5. Randbedingungen

- Alle Regeln aus Teil A gelten weiter (Hard Rule 1, Herkunft, nur eigene Songs, −14 LUFS,
  ≤ −1 dBTP, Stereo).
- Claude läuft über eine **eigene Shim** mit eigenem Tagesbudget, nicht über openclaw
  (openclaw-Anfragen werden als „extra usage" abgerechnet, `claw/shim/README.md`).
- Ein Werkzeugaufruf darf `MCP_TOOL_TIMEOUT` (900 s beim Video-Claw) nicht reißen. Lange
  Arbeit läuft als Auftrag im Hintergrund.
- Laura läuft auf dem Mini-PC und hat **keinen Upload**. Sie holt Dateien nur per
  `source_url`. Ein `.wav`-Link geht am yt-dlp vorbei direkt in den Download
  (`ingest/ytdlp.py:48-52`, `ingest/handlers.py:420-424`); der Dateiname kommt aus
  `display_name`, der deshalb auf `.wav` endet.
- Ein Audio-Clip wird erst angenommen, wenn das Asset geprobt ist (Typ `audio` oder
  `codec_audio`), sonst 422 (`api/audio.py:38-44`).
- Lauras Render mischt Audio-Clips der Zeitleiste in jeden Export ein, auch beim Reel
  (`render/handlers.py:127, 403`). Ein Reel mit `max_duration_seconds` wird am Ende gekürzt
  (`handlers.py:359-370`).
- Speicher am PC ist knapp, und die Grenze wandert (`CommitLimit`, am 23.11. einmal 68,4 GB
  statt 79,7). Höchstens ein Musik-Auftrag zur Zeit; vor jedem Start gegen das **aktuelle**
  `CommitLimit` messen, Abstand mindestens 8 GB.
- Ports nur nach `python scripts/port_kollisionen.py --alle` und mit Eintrag in der Registry.
- Keine privaten Rechnernamen oder Adressen im öffentlichen `vibemind-os`; die kommen aus der
  `.env`.
- Dienste starten, beim OpenFang-Hub anmelden, Firewall-Regel setzen und den Video-Claw im
  Betrieb umstellen sind Eingriffe in den Betrieb: jeweils erst nach Felix' Ja zu diesem
  Schritt, mit WORKBOARD-Claim.

## 6. Wege

| Weg | Was | Abwägung |
| --- | --- | --- |
| A | Musik als Space im Register, Chat über das Gehirn der App | kein zweiter Chat, aber ein Planungsschritt dazwischen |
| B | eigener Musik-Claw mit Shim, Budget und Chat-Kanal | flexibler im Gespräch, ein Claude-Prozess mehr |
| C | Laura bestellt die Musik selbst | verlangt Umbau von Laura, ausgeschieden |

**Felix hat B gewählt, zusätzlich A** (24.11.). Chat-Kanal von B ist ein eigener
Telegram-Bot. Beide Wege und der Video-Claw nutzen denselben Musik-Dienst.

## 7. Aufbau

```text
Felix (Telegram)        Felix (App-Chat)            Video-Claw (Reel-Auftrag)
      │                       │                              │
Telegram-Bot              Gehirn → OpenFang-Hub              │ Python, HTTP-MCP
      │                       │ (schmale Werkzeugliste)       │
Musik-Claw-Shim               │                              │
      │ MCP (volle Liste)     │                              │
      ▼                       ▼                              ▼
            Musik-Dienst „musik-studio" (am PC, 127.0.0.1)
              ├─ Auftrags-Läufer: ein Auftrag zur Zeit, dj-set als Unterprozess
              ├─ Datei-Link: signiert, 15 min gültig, nur für den Mini-PC erreichbar
              └─ Laura-Anschluss: importieren, warten, Audio-Clip anlegen
                                       │
                                       ▼
                          Laura (Mini-PC, LAURA_API_URL, LAURA_TOKEN)
```

### 7.1 Musik-Dienst `musik-studio`

Ort: `spaces/musik/studio/dienst/`, Python aus `studio/.venv`. FastMCP, streamable-http,
gebunden an `127.0.0.1`, mit `/health`; jedes Werkzeug läuft im Thread (Muster
`spaces/marketing/claw/mcp_huelle.py`, sonst friert der Dienst ein wie :8137 am 21.10.).

| Werkzeug | Tut | Sichtbar für |
| --- | --- | --- |
| `bestand_zeigen` | Songs mit Tempo, Tonart, Länge, Herkunft | alle |
| `song_aufnehmen` | Datei aus `MUSIK_DATEN/eingang/` in den Bestand, mit Herkunft | nur Musik-Claw |
| `set_bestellen` | legt einen Set-Auftrag an, kehrt sofort mit Auftrags-ID zurück | alle |
| `videomusik_bestellen` | Auftrag für Videomusik (Sekunden oder Frames+fps oder Laura-Zeitleiste) | alle |
| `auftrag_zeigen` | Zustand, Wahl, Befunde, Pfade, Bericht in Kurzform | alle |
| `auftrag_abbrechen` | beendet einen laufenden Auftrag | nur Musik-Claw |
| `in_laura_legen` | legt die Musik eines fertigen Auftrags in eine genannte Laura-Zeitleiste | alle |
| `laura_videos_suchen` | findet Laura-Projekte und ihre Zeitleisten über ein Suchwort (Name, Länge, letzter Export) | alle |
| `laura_rendern` | rendert eine Zeitleiste in Laura, wartet, lädt das Ergebnis in den Auftrags-Ordner | nur Musik-Claw |

Die Bestell-Werkzeuge nehmen dieselben Eingaben wie die CLI (`dj-set set`, `dj-set video`)
und geben keine freien Pfade an. Der Agent sieht in `auftrag_zeigen` Dateinamen, keine
Pfade.

`videomusik_bestellen` mit `laura_timeline_id`: der Dienst liest Länge und Bildrate der
Zeitleiste aus Laura und bestellt genau diese Länge in Frames.

### 7.2 Auftrags-Läufer

- Ein Auftrag zur Zeit. Videomusik-Aufträge stehen in der Warteschlange vor wartenden Sets
  (ein Reel wartet sonst auf ein 45-Minuten-Set); ein laufendes Set wird nicht unterbrochen.
- Jeder Auftrag läuft als Unterprozess `dj-set set|video` mit `--auftrag <id>` bzw. dem
  Lauf-Ordner aus dem Auftrag. Abbrechen heißt Unterprozess beenden.
- Zustand je Auftrag in `MUSIK_DATEN/auftraege/<id>/auftrag.json`: `wartet`, `laeuft`,
  `fertig`, `fertig_mit_befund`, `gescheitert`, `abgebrochen`, mit Grund und Zeitstempeln.
  Ergebnis ist der Lauf-Ordner aus Plan 1/2.
- Zeitlimit je Auftrag (Vorschlag: Video 10 min, Set 45 min). Wer es reißt, ist `gescheitert`
  mit Grund.
- Nach einem Neustart des Dienstes werden Aufträge im Zustand `laeuft` zu `gescheitert`
  („Dienst neu gestartet"); `wartet` läuft weiter.
- Vor jedem Start misst der Läufer `CommittedBytes` gegen das aktuelle `CommitLimit`. Unter
  8 GB Abstand wartet der Auftrag (Zustand bleibt `wartet`, Grund steht dabei).

### 7.3 Datei-Link für Laura

- Ein zweiter, kleiner HTTP-Zuhörer im Musik-Dienst, gebunden an die Adresse, die der
  Mini-PC erreicht (`MUSIK_LINK_HOST`, aus der `.env`).
- Ein Link ist `GET /datei/<auftrag>/<name>?ablauf=<ts>&sig=<hmac>`, HMAC über Auftrag,
  Name und Ablauf mit `MUSIK_LINK_SCHLUESSEL`, gültig 15 min. Nur `videomusik.wav` und
  `videomusik.mp3` fertiger Aufträge. Vorbild: `medien-link` in `marketing/api/video.py:539-553`.
- Windows-Firewall: eine Regel für genau diesen Port und genau die Adresse des Mini-PC.

### 7.4 Laura-Anschluss (`in_laura_legen`)

1. Auftrag muss `fertig` sein und ein `laura`-Block im `videomusik.json` haben.
2. Zeitleiste lesen. Weicht ihre Bildrate von `fps_num/fps_den` ab, Frames aus `sekunden`
   neu rechnen. Weicht die Länge um mehr als einen Frame ab: Fehler „Musik passt nicht zur
   Zeitleiste (X statt Y Frames)", nichts wird angelegt.
3. Importieren: `POST /projects/{pid}/assets/import` mit `source_url` (Link aus 7.3) und
   `display_name` = `<titel>.wav`.
4. Warten, bis das Asset geprobt ist (Muster `laura_api.asset_warten`), höchstens 5 min.
5. `POST /timelines/{tid}/audio-clips` mit dem `laura`-Block plus `asset_id`. Lautstärke
   (`gain_percent`) aus dem Parameter des Werkzeugs (Vorgabe siehe 7.5). `ducking_percent`
   bleibt immer 100: Laura senkt damit nur die Grundspur, nicht die Musik, und bei Reels mit
   Originalton wäre die Grundspur der Sprecher (`render/mp4.py:866-884`).
6. Liegt auf der Zeitleiste schon ein Musik-Clip mit demselben Auftrag im Label, wird nichts
   doppelt angelegt. Vorhandene Clips werden nie gelöscht oder verändert.
7. Rendern macht `in_laura_legen` nicht. Das tut der Aufrufer: der Video-Claw mit
   `render-reel`, der Musik-Claw mit `laura_rendern` (7.4a).

### 7.4a Video finden und zurückschicken

- **Finden:** `laura_videos_suchen(suchwort)` liest Projekte und Zeitleisten aus Laura und
  gibt Treffer mit Projektname, Zeitleiste, Länge in Sekunden und Datum des letzten Exports
  zurück. Bei mehr als einem Treffer fragt der Musik-Claw nach, welches Video gemeint ist. Er
  rät nie, und ohne Treffer legt er nichts an.
- **Rendern:** `laura_rendern(timeline_id, auftrag)` startet den Render
  (`POST /timelines/{tid}/render`). Hatte die Zeitleiste schon einen Export, nimmt es
  dieselben Export-Optionen, damit ein Reel ein Reel bleibt (Untertitel, Format, Logo). Es
  wartet höchstens 15 min, holt `GET /exports/{id}/download-url` (mit Wiederholen, bis der Link
  da ist) und lädt die Datei als `laura-export.mp4` in den Auftrags-Ordner. Der Aufruf kehrt
  wie die Bestellungen sofort zurück; das Rendern ist ein eigener Schritt im Auftrag, sichtbar
  in `auftrag_zeigen`.
- **Zurückschicken:** Ist der Auftrag mit Render fertig, schickt der Telegram-Bot das Video in
  den Chat. Über 50 MB schickt er den Kurzbericht und Lauras Download-Link (nur im eigenen Netz
  erreichbar) statt der Datei.
- Ein Render überschreibt in Laura nichts; es entsteht ein neuer Export.

Laura-Zugang wie beim Video-Claw: `LAURA_API_URL`, `LAURA_TOKEN` (Header `X-Laura-Token`),
aus der Root-`.env` nachgeladen.

### 7.5 Video-Claw

Heute baut `reel_bauen` (`spaces/marketing/video_claw/werkzeuge.py:312-398`) zuerst die
Zeitleiste (`narrated-reel`, `render: False`, bis 600 s) und startet dann `render-reel`.
Die Musik kommt genau dazwischen.

Weil `narrated-reel` plus Musik zusammen die 900 s eines Aufrufs reißen können, teilt sich
das Reel in zwei Werkzeuge:

- `reel_bauen(..., musik="")`: baut die Zeitleiste wie heute. Ist `musik` leer oder `aus`,
  rendert es wie heute. Sonst bestellt es Videomusik für genau diese Zeitleiste (Wunsch =
  `musik`, vom Agenten aus dem Briefing formuliert) und gibt `timeline_id` und
  `musik_auftrag` zurück, ohne zu rendern.
- `reel_fertigstellen(timeline_id, musik_auftrag, ...)`: wartet auf die Musik (höchstens
  10 min; der ganze Lauf des Video-Claw hat 28 min, `CLAW_ZEITLIMIT_S`), ruft `in_laura_legen`, startet `render-reel` und gibt `export_id` zurück. Scheitert
  die Musik, rendert es das Reel **ohne** Musik und nennt den Grund als Warnung. Ein Reel geht
  nie wegen der Musik verloren.

Das `AGENTS.md` des Video-Claw bekommt die Regel: Musik immer, außer der Auftrag sagt „ohne
Musik"; der Wunsch folgt Ton und Marke des Briefings, Gesang aus.

**Lautstärke unter Sprache (meine Festlegung):** Laura hat kein Absenken, das auf Sprache
reagiert. Die Sprecherstimme eines Reels ist selbst eine Überlagerung (`replace_original`),
`ducking_percent` wirkt also nicht auf die Musik. Die Musik liegt deshalb mit festem Pegel
unter dem ganzen Reel: `gain_percent` 25 (rund −12 dB), bei Reels ganz ohne Sprache 100.
Ob das reicht, entscheidet Felix' Ohr in Abnahme 7. Ein Absenken je Satz (Lautstärkekurve in
der WAV aus den Sprechbereichen) ist ausdrücklich nicht Teil von Plan 3.

**Kürzen:** `reel_bauen` setzt heute kein `max_duration_seconds`, Laura kürzt das Reel also
nicht. Die Musik wird für die volle Zeitleiste bestellt. Wer später eine Längengrenze für
Reels einführt, muss die Musik mit anpassen.

### 7.6 Musik-Claw (Telegram)

- **Shim:** eigene Instanz nach `spaces/marketing/claw/shim/marketing_shim.py` mit
  `VIBEMIND_AGENT=musik-claw`, `SHIM_EXTRA_MCP_CONFIG` auf `musik-mcp.json` (volle
  Werkzeugliste, `timeout` 1200000), `MCP_TOOL_TIMEOUT=900000`.
- **Agent:** `spaces/musik/claw/AGENTS.md`: Rolle, Werkzeuge, Regeln (nur eigene Songs, keine
  Pfade, Bestellung sofort bestätigen, Fortschritt melden, nichts in Laura ohne genannte
  Zeitleiste).
- **Bot:** nach `spaces/marketing/workers/telegram_bot.py`: Long-Polling, nur Felix'
  Chat-ID, eigener Token `MUSIK_TELEGRAM_TOKEN` in der Root-`.env`. Felix legt den Bot bei
  BotFather selbst an.
- **Dateien in den Chat:** Wird ein Auftrag aus diesem Chat fertig, schickt der Bot die MP3
  (bei Sets die MP3 des Sets) und, falls vorhanden, `video-mit-musik.mp4`, mit Kurzbericht.
  Telegram erlaubt Bots 50 MB je Datei; größer: nur Bericht und Hinweis.
- **Budget:** zwei Einträge in `config/agent_budget.yaml` des äußeren Repos: `musik-claw`
  (Chat) und `musik-kette` (Sprachmodell-Aufrufe von `djset`). Bei Überschreitung sagt der
  Bot das freundlich und bestellt nichts.

### 7.7 Chatfenster der App (Weg A)

- Neuer Eintrag `musik` in `config/space_agent_registry.yml` mit Agent `brain-musik`,
  `mcp_servers: [musik-studio]` und Fähigkeiten `musik.set.bestellen`,
  `musik.video.bestellen`, `musik.auftrag.zeigen`, `musik.laura.unterlegen`, jeweils
  `execution: { kind: mcp, server: musik-studio }`.
- Anmeldung beim OpenFang-Hub an beiden Stellen (`openfang/openfang.vibemind.toml` und
  `~/.openfang/config.toml`), geprüft mit `scripts/verify_openfang_mcp_registration.py`.
- Der Agent `brain-musik` bekommt über `mcp_tools` im Register nur die Werkzeuge der Spalte
  „alle" in 7.1 (Muster: Eintrag `bubbles`). Ein Endpunkt genügt. `song_aufnehmen` nimmt
  ohnehin nur Dateien aus dem Eingangsordner, in den nur Felix legt.
- Wie bei den anderen Fähigkeiten mit `kind: mcp` verlangt das Register
  `required_provenance: [approval_ref, cost_ref]`. Musik-Bestellungen aus der App laufen
  durch denselben Freigabe- und Kostenweg des Gehirns wie Lernen oder Desktop; Plan 3 baut
  keinen eigenen. Der Plan prüft als Erstes, wie eine Lern-Fähigkeit diese beiden Belege
  heute bekommt, und macht es genauso.

### 7.8 Start und Betrieb

- Eigenes Startskript `spaces/musik/studio/scripts/musik-dienste-starten.ps1`, idempotent,
  Start per `Start-Process` mit Ausgabe in Dateien: Musik-Dienst, Musik-Shim, Telegram-Bot.
- Ports (Vorschlag, vor dem Eintragen per `port_kollisionen.py` prüfen): 8139 Musik-Dienst,
  8141 Musik-Shim, 8142 Telegram-Bot (`/health`), 8146 Datei-Link.
- Ein Wächter, der tote Dienste neu startet, nach dem Muster von
  `spaces/marketing/claw/dienst_waechter.py`.

## 8. Abnahme

| | Schritt | Fertig, wenn |
| --- | --- | --- |
| 1 | WAV-Probe | eine `videomusik.wav` kommt über den signierten Link in einem Testprojekt von Laura als Asset vom Typ `audio` an |
| 2 | In Laura | `in_laura_legen` legt die Musik in eine Testzeitleiste; der Export enthält sie, Länge unverändert auf den Frame |
| 3 | Reel mit Musik | ein echter Reel-Auftrag des Video-Claw kommt mit Musik heraus; Sprecher verständlich, Reel-Länge wie ohne Musik |
| 4 | Telegram | Felix bestellt im Telegram-Chat eine Videomusik und ein Set; beide Dateien kommen im Chat an |
| 4a | Laura-Video per Name | Felix schreibt „leg Musik unter <Video>"; der Claw findet es (oder fragt nach), legt Musik ein, Laura rendert, das Video kommt im Chat an |
| 5 | App | ein Wunsch im App-Chat erzeugt einen Musik-Auftrag über das Gehirn |
| 6 | Grenzen | Budget erschöpft → freundliche Absage; abgelaufener Link → 403; falsche Zeitleistenlänge → Fehler, nichts angelegt |
| 7 | Gehört | Felix hat das Reel aus Schritt 3 und eine Telegram-Bestellung angehört |

## 9. Meine Festlegungen (nicht von Felix entschieden)

- „und a" gelesen als: Telegram-Bot **und** Weg A (App-Chat über das Gehirn), keine eigene
  Musik-Seite in der App.
- Video-Claw: Musik für jedes Reel, außer „ohne Musik"; Gesang aus.
- Reel in zwei Werkzeugen (`reel_bauen`, `reel_fertigstellen`) statt eines längeren Timeouts.
- Scheitert die Musik, wird das Reel ohne Musik gerendert, mit Warnung.
- Musik unter Sprecher bei `gain_percent` 25, bis die Probe etwas Besseres zeigt.
- Auftrag als Unterprozess, ein Auftrag zur Zeit, Speichermessung vor dem Start.
- Datei-Link: HMAC, 15 min, nur Mini-PC per Firewall.
- Ein MCP-Endpunkt, Einschränkung für die App über `mcp_tools` im Register.
- App-Bestellungen nutzen den vorhandenen Freigabe- und Kostenweg des Gehirns.
- Videomusik vor Sets in der Warteschlange; ein laufendes Set wird nicht unterbrochen.
- Speicherabstand 8 GB gegen das aktuelle `CommitLimit`.
- Ports 8139/8141/8142/8146 als Vorschlag.

## 10. Dem Bauenden überlassen

- Ob `dj-set` für Aufträge einen eigenen Unterbefehl oder Parameter bekommt, um den
  Lauf-Ordner zu setzen und Fortschritt zu melden.
- Wie der Telegram-Bot vom Fertigwerden erfährt (Nachfragen beim Dienst in Abständen genügt).
- Wie `laura_rendern` die Export-Optionen des letzten Exports wiederverwendet (welcher
  Endpunkt, `render` oder `render-reel`, je nach Art des letzten Exports).
- Wie das Budget der Kette (`musik-kette`) in `djset` geprüft wird (dieselbe Prüfung wie in
  der Shim, `scripts/claude_budget.py`).
