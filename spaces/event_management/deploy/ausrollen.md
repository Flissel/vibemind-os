# Ausrollen des Event-Systems (Etappe 4, Teil B)

Anleitung zu `deploy/ausrollen.sh`. Das Skript rollt in Phasen aus; jede Phase hat ein
eigenes OK von Felix, einen eigenen Rückweg und ein Abbruchkriterium. Nichts davon läuft
von selbst: ohne `--ich-habe-ok` tut das Skript nichts (Exit 2).

```
bash deploy/ausrollen.sh --ich-habe-ok <phase> [argument]
bash deploy/ausrollen.sh --ich-habe-ok zurueck <phase> [argument]
```

Gearbeitet wird auf dem Deploy-Host (der VM) im Repo unter `/home/debian/vibemind-os`
(genau dieser Pfad steht in `bereitstellung.service` und `nachrichten.service`, `pruefen`
vergleicht ihn). Vor jeder Phase fragt der Controller Felix: „Phase X jetzt? Rückweg: …“.
Nach jeder Phase steht ein Ledger-Eintrag, der nur Ergebnisse nennt, keine IDs.

## Vorher

- **Claim** im WORKBOARD bzw. in der maschinenweiten Koordination eintragen und committen,
  dann den Verweis ablegen: `echo "WORKBOARD <Datum>" > laufzeit/ausrollen/claim`. Ohne
  diese Datei besteht `pruefen` nicht.
- **`umask 077`** in der Shell, in der ausgerollt wird. Das Skript setzt sie selbst; für
  die Befehle dieser Anleitung gilt sie genauso.
- **Dateien unter `laufzeit/`** (0600, nie im Git): `admin-dsn` (DSN der Rolle
  `supabase_admin`, nur hier und in `EVENT_ADMIN_DSN_FILE`), `event-app-dsn`,
  `event-sitzung-geheimnis`, `event-telegram-token`, `event-smtp-passwort`,
  `event-app.env`. Dazu `werte.local.json` (aus `werte.beispiel.json`, alle Schlüssel
  gefüllt).
- **Host-Dateien** für die Timer: `/etc/event-management/bereitstellung.env` und
  `nachrichten.env` (0600; Inhalt siehe Kopf der `.service`-Dateien).
- **Host-Python.** `bereitstellung` und `nachrichten` laufen als Systemdienste mit
  `/usr/bin/python3 -m spaces.event_management.system.<x>`. Der Host-Python braucht die
  Pakete aus `spaces/event_management/requirements.txt` (psycopg, psycopg-pool, PyYAML,
  …). `pruefen` meldet fehlende Module; installiert wird mit OK von Hand
  (`python3 -m pip install -r spaces/event_management/requirements.txt`, bzw. die
  Distribution-Pakete).
- **Netz.** Das Netz `event-agent` bleibt ohne `ip_range`. `event-mcp` nimmt die feste
  Adresse `10.250.10.14`; `pruefen` liest mit `docker network inspect`, ob ein anderer
  Container sie hält (dann diesen Container neu starten, bevor es weitergeht).
- **Images von Shim und Claw** sind schon da (`event-management/shim:…` aus dem
  bewerbung-Bestand, `ghcr.io/openclaw/openclaw:…`). `system` und `web` baut `bauen`.

### Tags (eine Stelle)

Das Compose verlangt `EVENT_SYSTEM_TAG` und `EVENT_WEB_TAG` bei **jedem** Aufruf, auch bei
Diensten ohne Profil. Das Skript hält sie in der nicht geheimen Datei
`laufzeit/ausrollen/tags.env`: `bauen` legt sie beim ersten Aufruf an (Git-Kurz-SHA des
Repos, oder die beiden Variablen aus der Umgebung), jede andere Phase liest sie und
exportiert sie. Weicht eine gesetzte Variable ab: Abbruch. Wer `deploy/anbinden.sh` oder
`deploy/messen.sh` von Hand startet, exportiert beide Variablen zuvor selbst, etwa
`set -a; . laufzeit/ausrollen/tags.env; set +a`.

## Reihenfolge, OK-Punkte, Abbruch, Rückweg

Jede Phase schreibt `laufzeit/ausrollen/<phase>.ok` und verlangt die Stempel ihrer
Vorphasen. Fehlt einer, bricht sie mit dem Namen der Vorphase ab. Rückwege gehen nur
rückwärts: Solange eine spätere Phase gestempelt ist, verweigert `zurueck` die frühere.

### 1. `pruefen`  (`--ich-habe-ok pruefen`)

Misst ohne Änderung: PostgreSQL ≥ 15, Images (Shim, Claw), `werte.local.json` vollständig,
`laufzeit/`-Dateien da und 0600, Claim gesetzt, `10.250.10.14` frei, Host-Python mit den
Paketen aus `requirements.txt`, Repo-Pfad wie in den `.service`-Dateien, Host-Env-Dateien.

- **OK von Felix:** nein (nur Lesen); der Controller meldet das Ergebnis.
- **Abbruch:** jede Zeile `FEHLT`. Erst beheben, dann wiederholen.
- **Rückweg:** `--ich-habe-ok zurueck pruefen` (nur der Stempel fällt).

### 2. `sichern`  (`--ich-habe-ok sichern`)

`pg_dump -n event -Fc` nach `laufzeit/sicherung/event-<zeit>.dump`, dazu eine Kopie der
`openclaw.json` des Claw (`openclaw-<zeit>.json`, enthält Schlüssel, 0600). Gibt es den
Claw noch nicht, entsteht eine Marke `openclaw-<zeit>.keine`.

- **OK von Felix:** ja.
- **Abbruch:** `pg_dump` scheitert oder die Sicherung ist leer.
- **Rückweg:** `--ich-habe-ok zurueck sichern` (Stempel fällt, Dateien bleiben liegen).

### 3. `migrieren`  (`--ich-habe-ok migrieren`)

`db/anwenden.py` als `supabase_admin`; die DSN kommt nur aus `EVENT_ADMIN_DSN_FILE`
(Vorgabe `laufzeit/admin-dsn`). Das Skript prüft vorher Rolle und PG ≥ 15.

- **OK von Felix:** ja.
- **Abbruch:** andere Rolle als `supabase_admin`, PG < 15, jede Migration mit Fehler (jede
  läuft in eigener Transaktion; die Sicherung aus Phase 2 liegt bereit).
- **Rückweg:** `--ich-habe-ok zurueck migrieren` führt **nichts** aus, es nennt den Weg.
  Das Schema `event` ist neu; ein Löschen ist destruktiv und braucht ein eigenes OK:
  `DROP SCHEMA event CASCADE;` und bei Bedarf `pg_restore` aus der Sicherung. Danach den
  Stempel `laufzeit/ausrollen/migrieren.ok` von Hand entfernen.

### 4./5. `bauen system`, `bauen web`  (`--ich-habe-ok bauen system`, danach `--ich-habe-ok bauen web`)

`bauen` baut **genau ein** Image je Aufruf, gedeckelt: klassischer Builder
(`DOCKER_BUILDKIT=0`) mit `--memory` und `--memory-swap` (Vorgabe 4g,
`AUSROLLEN_BAU_SPEICHER`). Vor dem Bauen muss `MemAvailable` ≥ 1,5 GiB sein. Beide Aufrufe
laufen nacheinander, nie parallel (BuildKit-Race, ungedeckelte Builds trieben die VM schon
in Swap-Not).

- **OK von Felix:** ja, je Image.
- **Abbruch:** `MemAvailable` fällt unter 1,5 GiB oder Swap belegt sich neu, Build-Fehler.
- **Rückweg:** `--ich-habe-ok zurueck bauen system` bzw. `zurueck bauen web` löscht das Image
  (`docker rmi`); das geht nur, wenn kein Container es mehr nutzt (erst `zurueck dienste`).

### 6. `shim`  (`--ich-habe-ok shim`)

Der Shim backt `claude_budget.py` beim Bauen ein (`spaces/rowboat/deploy/headless/shim/
Dockerfile`, Zeile `COPY … claude_budget.py …`); ein Mount ist es nicht. Ohne neuen Bau
kennt der laufende Shim `warnen_ab` nicht und warnt nie. Quelle des Image
`event-management/shim:…` ist ein vom Controller umgetaggtes Image aus dem
bewerbung-bot-Bestand; **der Baukontext liegt nicht in diesem Repo.**

- **Mit Baukontext:** `EVENT_SHIM_BAUKONTEXT=<Ordner>` setzen (Dockerfile, die Shim-Dateien
  und das neue `scripts/claude_budget.py` des äußeren Repos darin). Die Phase tagt das
  bisherige Image als `…:vorher`, merkt sich dessen Id in `laufzeit/ausrollen/shim.vorher`,
  baut gedeckelt unter dem alten Namen und legt **nur den Shim** neu an
  (`docker compose up -d --no-deps --force-recreate event-management-shim`).
- **Ohne Baukontext:** die Phase prüft nur, dass das erwartete Image da ist, und legt den
  Shim neu an. Sie sagt es laut: das eingebackene `claude_budget.py` bleibt alt.
- **OK von Felix:** ja (der Shim startet neu, kurze Unterbrechung der Claude-Aufrufe).
- **Abbruch:** der Shim wird nicht `healthy` (60 s).
- **Rückweg:** `--ich-habe-ok zurueck shim` setzt den Tag auf das vorherige Image zurück
  und legt den Shim neu an.

Das Budget-Verhalten: Ab `warnen_ab: 0.8` (also 240 von 300 Aufrufen) schreibt der Wächter
einmal je Tag eine Zeile `{"art": "warnung", "agent", "heute", "grenze"}` in das Tagesprotokoll
unter `laufzeit/shim-budget/`. Dazu ruft er `budget_warnung`; im Shim-Container ist das ohne
`VIBEMIND_BUDGET_WARNEN_CMD` eine WARNING-Logzeile (der Container hat weder
`notify_telegram.py` noch ein Telegram-Token und bekommt keins). Am PC kann
`VIBEMIND_BUDGET_WARNEN_CMD="python scripts/notify_telegram.py Tagesbudget-Warnung"` die
Meldung nach Telegram schicken.

### 7. `dienste`  (`--ich-habe-ok dienste`)

`docker compose --profile event-system up -d event-api event-mcp event-web`, danach Proben:
`event-mcp` `/gesund` = 200, `event-api` `/api/ich` und `event-web` `/` antworten unter 500.

- **OK von Felix:** ja.
- **Abbruch:** eine Probe schlägt nach 60 s fehl; `event-mcp` startet nicht („Address
  already in use“ auf `10.250.10.14`).
- **Rückweg:** `--ich-habe-ok zurueck dienste` = `docker compose --profile event-system stop
  event-web event-mcp event-api` (stoppen, nichts löschen).

### Schlüssel ausstellen (zwischen `dienste` und `anbinden`)

Vier Schlüssel; die Klartexte gehen **direkt in 0600-Dateien**, nie auf den Bildschirm
(die CLI schreibt den Klartext auf stdout und die Schlüssel-ID auf stderr; darum immer mit
`>` umlenken). Auf dem Host, im Repo:

```
umask 077
mkdir -p laufzeit/schluessel
export EVENT_ADMIN_DSN_FILE=laufzeit/admin-dsn
python3 -m spaces.event_management.system.schluessel ausstellen --art melden --bezeichnung "Empfang main" > laufzeit/schluessel/melden
python3 -m spaces.event_management.system.schluessel ausstellen --art betreiber_lesen --bezeichnung "Brain lesen" --name VibeMind > laufzeit/schluessel/bn-lesen
python3 -m spaces.event_management.system.schluessel ausstellen --art betreiber --bezeichnung "Brain Auftrag" --name VibeMind > laufzeit/schluessel/bn-auftrag
python3 -m spaces.event_management.system.schluessel ausstellen --art betreiber_lesen --bezeichnung "Pruefung" --name Pruefung > laufzeit/schluessel/pruefung
```

- `melden`: Zeile 5 von `anbinden` (Server `melden`, Agent `main`).
- `betreiber_lesen` mit Name `VibeMind`: Zeile 6 (`bn-lesen`).
- `betreiber` mit Name `VibeMind`: Zeile 7 (`bn-auftrag`).
- `betreiber_lesen` mit Name `Pruefung`: für den Beobachter des Brain
  (`EVENT_VERLAUF_TOKEN_FILE`). Die Datei geht per 0600-Datei dorthin, wo der Beobachter
  läuft, nie per Chat oder Mail.

Die Schlüssel-IDs erscheinen auf dem Terminal (nicht geheim); sie in den Ledger-Eintrag
nicht übernehmen. Zum Widerruf: `… schluessel widerrufen <id>`.

- **OK von Felix:** ja.
- **Abbruch:** eine Datei bleibt leer, oder ein Klartext taucht in einer Ausgabe auf (dann den
  Schlüssel sofort widerrufen).

### Vor `anbinden`: Arbeitsbereich vergleichen

`deploy/anbinden.sh` überschreibt bei **jedem** Lauf `SOUL.md`, `AGENTS.md` und `skills` aller
Arbeitsbereiche. Der Live-Arbeitsbereich der Festival-Gruppe darf dabei nichts verlieren,
was nur dort steht. Darum vorher:

1. `python3 deploy/fuellen.py werte.local.json build/` (die gefüllte Saat).
2. Den Live-Arbeitsbereich aus dem Claw-Volume holen (falls dort schon einer liegt) und
   vergleichen, zunächst nur Namen:
   `docker cp event-management-claw:/home/node/.openclaw/workspace-legacy laufzeit/sicherung/workspace-live`
   und `diff -rq laufzeit/sicherung/workspace-live build/workspace-legacy`.
   Den Inhalt des Unterschieds nur lokal lesen, nicht in ein protokolliertes Terminal
   ausgeben. Ein noch ungenutztes Volume hat keinen: dann gegen den Arbeitsbereich des alten
   Festival-Containers vergleichen.
3. **Unterscheiden sie sich,** nicht anbinden. Entweder Felix bestätigt, dass die Saat
   gewinnen soll, oder die Live-Änderungen kommen (mit Platzhaltern statt privater Werte) in
   `config/workspace-legacy`, werden committet, und `fuellen.py` läuft neu. Die Kopie unter
   `laufzeit/sicherung/` bleibt als Rückweg.
4. Erst dann: `touch laufzeit/ausrollen/workspace-geprueft`. Ohne diese Datei verweigert
   `anbinden`.

### 8. `anbinden`  (`--ich-habe-ok anbinden`)

Ruft `deploy/anbinden.sh`; es liest **genau sieben Zeilen** von stdin: Gateway-Token,
Telegram-Token, Authorization der Event-App, Claude-Code-OAuth-Token (leer = bleibt),
dann `Bearer <melden>`, `Bearer <bn-lesen>`, `Bearer <bn-auftrag>`. Nichts davon als
Argument. Ohne Terminal-Ausgabe der Schlüssel, etwa:

```
{
  cat laufzeit/eingabe/gateway-token laufzeit/eingabe/telegram-token \
      laufzeit/eingabe/event-app-auth laufzeit/eingabe/claude-oauth
  for k in melden bn-lesen bn-auftrag; do printf 'Bearer %s\n' "$(cat "laufzeit/schluessel/$k")"; done
} | bash deploy/ausrollen.sh --ich-habe-ok anbinden
```

(`laufzeit/eingabe/` legt der Controller aus dem Tresor an, je Datei eine Zeile, 0600.)

- **OK von Felix:** ja, nach dem Arbeitsbereich-Vergleich.
- **Abbruch:** Arbeitsbereich unterscheidet sich, `healthz` des Claw antwortet nicht
  (60 s), der Shim antwortet nicht.
- **Rückweg:** `--ich-habe-ok zurueck anbinden` spielt die Sicherung der `openclaw.json`
  (Phase 2) in das Volume zurück und startet den Claw neu. Gab es vorher keinen Claw, wird
  er gestoppt; das Volume bleibt. Die in diesem Lauf ausgestellten Schlüssel danach mit
  `… schluessel widerrufen <id>` widerrufen.

### 9. `timer`  (`--ich-habe-ok timer`)

Installiert `bereitstellung` und `nachrichten` (Service + Timer) nach
`/etc/systemd/system` (mit `sudo`), `daemon-reload`, `enable --now` beider Timer.

- **OK von Felix:** ja.
- **Abbruch:** ein Timer wird nicht `active`; der erste Lauf scheitert (Journal ansehen,
  meist Host-Python ohne Paket oder falscher Repo-Pfad).
- **Rückweg:** `--ich-habe-ok zurueck timer` (Timer stoppen und deaktivieren, die vier
  Dateien entfernen, `daemon-reload`).

### 10. `tailnet`  (`--ich-habe-ok tailnet`)

`tailscale serve --bg --https=3170 http://127.0.0.1:3170` für `event-web`. Vor dem ersten
Lauf `tailscale serve --help` der installierten Version gegen diese Zeile prüfen (die
Schreibweise der Optionen wechselt zwischen Versionen).

- **OK von Felix:** ja, denn ab hier ist die Oberfläche im Tailnet sichtbar.
- **Abbruch:** `tailscale serve` meldet einen Fehler oder `status` zeigt etwas Unerwartetes.
- **Rückweg:** `--ich-habe-ok zurueck tailnet` = `tailscale serve --https=3170 off`.

## Wer misst was

| Was | Wer |
| --- | --- |
| Vorbedingungen (PG, Images, Dateien, Claim, `.14`, Host-Python) | `ausrollen.sh pruefen`, der Controller liest das Ergebnis |
| Sicherung vollständig (Dump nicht leer, `openclaw.json` da) | `ausrollen.sh sichern` |
| Migrationen angewendet | `db/anwenden.py` (nennt die Dateinamen) |
| Speicher beim Bauen (`MemAvailable`, Swap) | `ausrollen.sh bauen` vorher, der Controller beobachtet währenddessen |
| Gesundheit der Dienste | Proben in `dienste`; Shim `healthy` in `shim` |
| Arbeitsbereich-Vergleich | Controller, Entscheidung bei Felix |
| Schlüssel ausgestellt, nie sichtbar | Controller |
| Freigabe jeder Phase | Felix |

## Offen

- **Zustellung der Budget-Warnung an Felix.** Der Wächter im Shim-Container schreibt die
  Zeile `warnung` ins Tagesprotokoll unter `laufzeit/shim-budget/`. Dass der VM-Host sie
  liest und an Felix weitergibt (Telegram vom Host aus), ist ein **Folgeplan** und hier
  nicht gebaut. Der Shim bekommt dafür kein Telegram-Token.
- **Quelle des Shim-Images.** Der Baukontext (Dockerfile und Shim-Dateien) liegt im
  bewerbung-bot-Bestand außerhalb dieses Repos. Bis er in ein Repo wandert, braucht die
  Phase `shim` `EVENT_SHIM_BAUKONTEXT` von Hand.
- **`tailscale serve`-Syntax** gegen die installierte Version prüfen (siehe Phase 10).
- **`pg_dump`-Client** auf dem Host darf nicht älter sein als der Server (PG ≥ 15).
