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
  gefüllt). `admin-dsn` zeigt aus Sicht des Hosts auf die Datenbank, `event-app-dsn` aus
  Sicht der Container (`host=host.docker.internal`) und nennt die Login-Rolle der App, die
  `migrieren` anlegt (Phase 5), nie `event_app` selbst.
- **Host-Dateien** für die Timer: `/etc/event-management/bereitstellung.env` und
  `nachrichten.env` (0600; Inhalt siehe Kopf der `.service`-Dateien).
- **Python-Umgebung.** `bereitstellung` und `nachrichten` laufen als Systemdienste mit
  `/home/debian/vibemind-os/spaces/event_management/.venv/bin/python -m
  spaces.event_management.system.<x>`; dieselbe Umgebung nutzen das Skript und alle
  Handschritte unten (`$PY`). Der Debian-Python der VM ist "externally managed" und hat
  kein pip, deshalb eine eigene Umgebung (nicht im Git, `.venv/` ist ignoriert). Sie braucht
  das Debian-Paket `python3-venv` (fehlt es, scheitert `venv` mit „ensurepip is not
  available“; dann `sudo apt install python3-venv`, eigenes OK). Anlegen mit OK, im
  Repo-Wurzelordner:
  ```
  python3 -m venv spaces/event_management/.venv
  spaces/event_management/.venv/bin/pip install -r spaces/event_management/requirements.txt
  PY=/home/debian/vibemind-os/spaces/event_management/.venv/bin/python
  ```
  `pruefen` meldet eine fehlende Umgebung, fehlende Module und Units, die einen anderen
  Python starten. Rückweg: den Ordner `.venv` löschen.
- **Postgres-Werkzeuge.** `pruefen` und `sichern` brauchen `psql` und ein `pg_dump`, das
  mindestens die Hauptversion des Servers hat. Fehlen sie auf dem Host (oder ist der Client
  älter), nimmt das Skript sie aus einem Image: dessen Name kommt in die nicht geheime Datei
  `laufzeit/ausrollen/pg-image` (oder `EVENT_PG_IMAGE`). Am einfachsten das Image des
  Datenbank-Containers selbst, dann passt die Version immer:
  `docker inspect -f '{{.Config.Image}}' <db-container> > laufzeit/ausrollen/pg-image`.
  Das Skript ruft dann `docker run --rm -i --pull never --network host --env-file …
  -v …pgpass:/run/pgpass:ro --entrypoint psql|pg_dump <image>`: Host, Port, Rolle und
  Datenbank stehen in der Env-Datei, das Passwort nur in einer 0600-pgpass-Datei, die
  schreibgeschützt eingehängt wird (so landet es nicht in der Container-Konfiguration); beide
  werden nach dem Aufruf gelöscht, nie steht etwas im Argument. Ein fehlendes Image wird nicht
  nachgeladen. Der Dump geht über stdout in die Sicherungsdatei.
- **Netz.** Das Netz `event-agent` bleibt ohne `ip_range`. `event-mcp` nimmt die feste
  Adresse `10.250.10.14`; `pruefen` liest mit `docker network inspect`, ob ein anderer
  Container sie hält (dann diesen Container neu starten, bevor es weitergeht).
- **Images von Shim und Claw** sind schon da (`event-management/shim:…` aus dem
  bewerbung-Bestand, `ghcr.io/openclaw/openclaw:…`). `system` und `web` baut `bauen`, den
  Shim baut `shim` neu (Baukontext nach dem Rezept in Phase 6).

### Tags (eine Stelle)

Das Compose verlangt `EVENT_SYSTEM_TAG` und `EVENT_WEB_TAG` bei **jedem** Aufruf, auch bei
Diensten ohne Profil. Das Skript hält sie in der nicht geheimen Datei
`laufzeit/ausrollen/tags.env`: `bauen` (gleich nach `pruefen`) legt sie beim ersten Aufruf an (Git-Kurz-SHA des
Repos, oder die beiden Variablen aus der Umgebung), jede andere Phase liest sie und
exportiert sie. Weicht eine gesetzte Variable ab: Abbruch. Wer `deploy/anbinden.sh` oder
`deploy/messen.sh` von Hand startet, exportiert beide Variablen zuvor selbst, etwa
`set -a; . laufzeit/ausrollen/tags.env; set +a`.

## Reihenfolge, OK-Punkte, Abbruch, Rückweg

Jede Phase schreibt `laufzeit/ausrollen/<phase>.ok` und verlangt die Stempel ihrer
Vorphasen. Fehlt einer, bricht sie mit dem Namen der Vorphase ab. Rückwege gehen nur
rückwärts: Solange eine spätere Phase gestempelt ist, verweigert `zurueck` die frühere.

Feste Reihenfolge (Abschluss-Review F-3): `pruefen` → `bauen system` → `bauen web` →
**messen (Task 7)** → `sichern` → `migrieren` → `shim` → `dienste` → Schlüssel ausstellen →
`tailnet` → erster Betreiber, Firma und Legacy-Eintrag → `anbinden` → `timer` →
Brain-Umgebung. Die Schritte ohne Phasennamen sind Handarbeit mit eigenem OK; ihre
Anleitung steht unten an ihrer Stelle.

Jede Phase löscht gleich nach dem Schalter, vor ihrer Arbeit, ihren eigenen Stempel und die
aller späteren Phasen. Scheitert ein Wiederholungslauf, bleibt also kein alter Stempel stehen
und die nächste Phase verweigert mit dem Namen der fehlenden. Ausnahme: die beiden
`bauen`-Varianten verlangen nur `pruefen` und lassen einander stehen; erst die späteren
Phasen verlangen beide.

Alle Läufe, auch `zurueck`, nehmen eine nicht blockierende Sperre auf
`laufzeit/ausrollen/lock` (`flock`, util-linux); hält sie ein anderer Lauf, bricht das Skript
ab: nie parallel ausrollen.

Das Skript meldet laut (nur Namen), wenn ein `AUSROLLEN_*`-Test-Haken gesetzt ist; im
Betrieb setzt man keinen. `AUSROLLEN_BAU_SPEICHER` muss `<Zahl>m` oder `<Zahl>g` sein,
höchstens 8g.

### 1. `pruefen`  (`--ich-habe-ok pruefen`)

Misst ohne Änderung: PostgreSQL ≥ 15, Images (Shim, Claw), `werte.local.json` vollständig,
`laufzeit/`-Dateien da und 0600, Claim gesetzt, `10.250.10.14` frei, Python-Umgebung mit den
Paketen aus `requirements.txt`, Repo-Pfad wie in den `.service`-Dateien, Host-Env-Dateien.

- **OK von Felix:** nein (nur Lesen); der Controller meldet das Ergebnis.
- **Abbruch:** jede Zeile `FEHLT`. Erst beheben, dann wiederholen.
- **Rückweg:** `--ich-habe-ok zurueck pruefen` (nur der Stempel fällt).

### 2./3. `bauen system`, `bauen web`  (`--ich-habe-ok bauen system`, danach `--ich-habe-ok bauen web`)

`bauen` baut **genau ein** Image je Aufruf, gedeckelt: klassischer Builder
(`DOCKER_BUILDKIT=0`) mit `--memory` und `--memory-swap` (Vorgabe 4g,
`AUSROLLEN_BAU_SPEICHER`). Vor dem Bauen muss `MemAvailable` ≥ 1,5 GiB sein. Beide Aufrufe
verlangen nur `pruefen`, sind voneinander unabhängig und laufen nacheinander, nie parallel
(BuildKit-Race, ungedeckelte Builds trieben die VM schon in Swap-Not).

- **OK von Felix:** ja, je Image.
- **Abbruch:** `MemAvailable` fällt unter 1,5 GiB oder Swap belegt sich neu, Build-Fehler.
- **Rückweg:** `--ich-habe-ok zurueck bauen system` bzw. `zurueck bauen web` löscht das Image
  (`docker rmi`); das geht nur, wenn kein Container es mehr nutzt (erst `zurueck dienste`).
- **Unabhängig:** Ein Neubau von `system` lässt den Stempel von `web` stehen (und
  umgekehrt), löscht aber die Stempel aller späteren Phasen ab `sichern`. Jede spätere
  Phase verlangt beide.

### Danach: messen (Task 7)

Erst jetzt gibt es `laufzeit/ausrollen/tags.env` und das Image
`event-management/system:<tag>`, die `deploy/messen.sh` braucht. Die Messpunkte M1–M6
laufen nach `deploy/messen.md` (eigener Claim, eigenes OK, Testpaar). Erst wenn alle
bestanden sind oder eine Alternative entschieden und umgesetzt ist, geht es mit
`sichern` weiter.

### 4. `sichern`  (`--ich-habe-ok sichern`)

Zuerst prüft die Phase per `psql`, ob das Schema `event` existiert. Wenn ja, läuft
`pg_dump -n event -Fc` nach `laufzeit/sicherung/event-<zeit>.dump`. Wenn nicht (erster Lauf
auf einer Datenbank ohne das Schema), gibt es nichts zu sichern: Es entsteht die Marke
`event-<zeit>.keine-schema`, und die Phase besteht. Dazu kommt eine Kopie der `openclaw.json`
des Claw (`openclaw-<zeit>.json`, enthält Schlüssel, 0600). Gibt es den Claw noch nicht,
entsteht eine Marke `openclaw-<zeit>.keine`.

- **OK von Felix:** ja.
- **Abbruch:** die Schema-Prüfung oder `pg_dump` scheitert, oder die Sicherung ist leer.
- **Rückweg:** `--ich-habe-ok zurueck sichern` (Stempel fällt, Dateien bleiben liegen).

### 5. `migrieren`  (`--ich-habe-ok migrieren`)

`db/anwenden.py` als `supabase_admin`; die DSN kommt nur aus `EVENT_ADMIN_DSN_FILE`
(Vorgabe `laufzeit/admin-dsn`). Das Skript prüft vorher Rolle und PG ≥ 15.

Danach legt `deploy/pg_hilfe.py app-rolle` die **Login-Rolle der App** an: `event_app` ist
NOLOGIN, die Dienste verbinden sich mit der Rolle aus `laufzeit/event-app-dsn` (`user`
beginnt mit `event_dienst`, `password` mindestens 24 Zeichen). Die Rolle wird Mitglied
von `event_app`; gibt es sie schon, bekommt sie nur das Passwort aus der Datei neu. Das
Passwort geht als SCRAM-Verifier an die Datenbank, nie im Klartext. Eine vorhandene Rolle mit
Sonderrechten (Superuser, CREATEROLE …) oder mit Mitgliedschaften außer `event_app` fasst
das Skript nicht an.

- **OK von Felix:** ja.
- **Abbruch:** andere Rolle als `supabase_admin`, PG < 15, jede Migration mit Fehler (jede
  läuft in eigener Transaktion; die Sicherung aus Phase 4 liegt bereit).
- **Rückweg:** `--ich-habe-ok zurueck migrieren` führt **nichts** aus, es nennt den Weg.
  Das Schema `event` ist neu; ein Löschen ist destruktiv und braucht ein eigenes OK:
  `DROP SCHEMA event CASCADE;` und bei Bedarf `pg_restore` aus der Sicherung, dazu
  `DROP ROLE <login-rolle>;` (Name aus `laufzeit/event-app-dsn`). Danach den Stempel
  `laufzeit/ausrollen/migrieren.ok` von Hand entfernen.

### 6. `shim`  (`--ich-habe-ok shim`)

Der Shim backt `claude_budget.py` beim Bauen ein (`spaces/rowboat/deploy/headless/shim/
Dockerfile`, Zeile `COPY … claude_budget.py …`); ein Mount ist es nicht. Ohne neuen Bau
kennt der laufende Shim `warnen_ab` nicht und warnt nie. Das bisherige Image
`event-management/shim:…` ist ein vom Controller umgetaggtes Image aus dem
bewerbung-bot-Bestand; dessen Baukontext liegt nicht in diesem Repo. Darum stellt man den
Kontext nach diesem Rezept zusammen und nennt ihn in `EVENT_SHIM_BAUKONTEXT`.

**Rezept für den Baukontext** (auf dem Rechner mit beiden Repos, danach als Ordner auf den
Host; nur Dateien, keine Werte). Der Ordner liegt **außerhalb des Repos**: die Shim-Module
sind privat, das Repo ist öffentlich; liegt der Kontext im Repo, bricht die Phase ab:

```
umask 077
K=$HOME/event-shim-kontext
mkdir -p "$K"
# 1. Aus vibemind-os: Dockerfile, Start-Skript und Budget-Huelle des Rowboat-Shims.
cp spaces/rowboat/deploy/headless/shim/Dockerfile \
   spaces/rowboat/deploy/headless/shim/start.sh \
   spaces/rowboat/deploy/headless/shim/rowboat_shim_huelle.py "$K/"
# 2. Der Event-Shim lauscht auf 0.0.0.0:8125 (Compose-Healthcheck, SHIM_URL des Claw),
#    der Rowboat-Shim auf 127.0.0.1:8121: nur die CMD-Zeile der Kopie anpassen.
sed -i 's/"--host", "127.0.0.1", "--port", "8121"/"--host", "0.0.0.0", "--port", "8125"/' "$K/Dockerfile"
# 3. Aus dem äußeren Repo: claude_budget.py mit `budget_warnung` (Stand nach dem Budget-Commit).
cp <äußeres-repo>/scripts/claude_budget.py "$K/"
# 4. Shim und Werkzeugmodul: dieselben Dateien wie für den Rowboat-Shim (bauen.ps1,
#    Quelle am PC `~/.local/bin`, nicht im Repo).
cp <shim-quelle>/claude_code_openai_shim.py <shim-quelle>/claude_code_openai_tools.py "$K/"
# 5. agent_budget.yaml verlangt nur das COPY der Dockerfile; im Betrieb liest der Shim die
#    gemountete deploy/agent_budget.yaml (VIBEMIND_BUDGET_CONFIG im Compose).
cp spaces/event_management/deploy/agent_budget.yaml "$K/"
```

Die Phase prüft vor jedem Docker-Aufruf: alle sieben Dateien da (`Dockerfile`, `start.sh`,
`rowboat_shim_huelle.py`, `claude_code_openai_shim.py`, `claude_code_openai_tools.py`,
`claude_budget.py`, `agent_budget.yaml`), `claude_budget.py` hat `budget_warnung`, die CMD
der Dockerfile startet auf `0.0.0.0` und `8125`. Sonst Abbruch ohne Stempel.

- **Mit Baukontext** (`EVENT_SHIM_BAUKONTEXT=$HOME/event-shim-kontext`): Die Phase tagt das
  bisherige Image als `…:vorher` und merkt sich dessen Id in `laufzeit/ausrollen/shim.vorher`,
  aber nur, wenn es noch keine `shim.vorher` gibt oder der letzte Shim-Lauf gelang (so
  zerstört ein Wiederholungslauf nach einem gescheiterten Gesundheitstest den Rückweg nicht);
  danach baut sie gedeckelt unter dem alten Namen und legt **nur den Shim** neu an
  (`docker compose up -d --no-deps --force-recreate event-management-shim`).
- **Ohne Baukontext** bricht die Phase ab (Abschluss-Review F-5): kein Neuanlegen, kein
  Stempel. Ein Shim mit altem `claude_budget.py` wäre ein stiller Fehler (keine Warnung).
- **OK von Felix:** ja (der Shim startet neu, kurze Unterbrechung der Claude-Aufrufe).
- **Abbruch:** Baukontext fehlt oder ist unvollständig; der Bau scheitert; der Shim wird
  nicht `healthy` (150 s).
- **Rückweg:** `--ich-habe-ok zurueck shim` setzt den Tag auf das vorherige Image zurück
  und legt den Shim neu an.

Das Budget-Verhalten: Ab `warnen_ab: 0.8` (also 240 von 300 Aufrufen) schreibt der Wächter
einmal je Tag eine Zeile `{"art": "warnung", "agent", "heute", "grenze"}` in das Tagesprotokoll
unter `laufzeit/shim-budget/`. Dazu ruft er `budget_warnung`; im Shim-Container ist das ohne
`VIBEMIND_BUDGET_WARNEN_CMD` eine WARNING-Logzeile (der Container hat weder
`notify_telegram.py` noch ein Telegram-Token und bekommt keins). Am PC kann
`VIBEMIND_BUDGET_WARNEN_CMD="python scripts/notify_telegram.py Tagesbudget-Warnung"` die
Meldung nach Telegram schicken. Auf der VM liest der Host-Dienst `budget-melden` (Phase
`timer`) die Zeile und schickt sie per Telegram an Felix.

### 7. `dienste`  (`--ich-habe-ok dienste`)

`docker compose --profile event-system up -d event-api event-mcp event-web`, danach Proben:
`event-mcp` `/gesund` = 200, `event-api` `/api/ich` und `event-web` `/` antworten unter 500.

- **OK von Felix:** ja.
- **Abbruch:** eine Probe schlägt nach 60 s fehl; `event-mcp` startet nicht („Address
  already in use“ auf `10.250.10.14`).
- **Rückweg:** `--ich-habe-ok zurueck dienste` = `docker compose --profile event-system stop
  event-web event-mcp event-api` (stoppen, nichts löschen).

### Schlüssel ausstellen (nach `dienste`, vor `tailnet`)

Vier Schlüssel; die Klartexte gehen **direkt in 0600-Dateien**, nie auf den Bildschirm
(die CLI schreibt den Klartext auf stdout und die Schlüssel-ID auf stderr; darum immer mit
`>` umlenken). Auf dem Host, im Repo:

```
umask 077
mkdir -p laufzeit/schluessel
export EVENT_ADMIN_DSN_FILE=laufzeit/admin-dsn
"$PY" -m spaces.event_management.system.schluessel ausstellen --art melden --bezeichnung "Empfang main" > laufzeit/schluessel/melden
"$PY" -m spaces.event_management.system.schluessel ausstellen --art betreiber_lesen --bezeichnung "Brain lesen" --name VibeMind > laufzeit/schluessel/bn-lesen
"$PY" -m spaces.event_management.system.schluessel ausstellen --art betreiber --bezeichnung "Brain Auftrag" --name VibeMind > laufzeit/schluessel/bn-auftrag
"$PY" -m spaces.event_management.system.schluessel ausstellen --art betreiber_lesen --bezeichnung "Pruefung" --name Pruefung > laufzeit/schluessel/pruefung
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

### 8. `tailnet`  (`--ich-habe-ok tailnet`)

`tailscale serve --bg --https=3170 http://127.0.0.1:3170` für `event-web`. Vor dem ersten
Lauf `tailscale serve --help` der installierten Version gegen diese Zeile prüfen (die
Schreibweise der Optionen wechselt zwischen Versionen).

- **OK von Felix:** ja, denn ab hier ist die Oberfläche im Tailnet sichtbar. Sie wird
  gleich gebraucht: der erste Betreiber legt dort die Firma des Legacy-Events an.
- **Abbruch:** `tailscale serve` meldet einen Fehler oder `status` zeigt etwas Unerwartetes.
- **Rückweg:** `--ich-habe-ok zurueck tailnet` = `tailscale serve --https=3170 off`.

### Erster Betreiber, Firma und Legacy-Eintrag (nach `tailnet`, vor `anbinden`)

Ohne Betreiber kann sich niemand an der Oberfläche anmelden, und anlegen lässt er sich dort
auch nicht: die Datenbank erlaubt `betreiber = true` nur über einen Betreiber (RLS
`person_anlegen`). Den ersten legt darum eine kleine Admin-CLI mit der Admin-DSN an. Das
Passwort (mindestens 12 Zeichen, wie bei Einladungen) kommt per stdin aus einer 0600-Datei,
nie als Argument; am Terminal fragt die CLI verdeckt. Auf dem Host, im Repo:

```
umask 077
export EVENT_ADMIN_DSN_FILE=laufzeit/admin-dsn
"$PY" -m spaces.event_management.system.betreiber anlegen --mail <mail> --name "<Name>" \
  < laufzeit/eingabe/betreiber-passwort
rm laufzeit/eingabe/betreiber-passwort
```

Ausgabe: nur die Person-ID (stdout) und ein fester Text (stderr), nie Mail, Name, Passwort
oder DSN. Gibt es die Mail schon, wird diese Person Betreiber; ein vorhandenes Passwort bleibt
unverändert. Ein zweiter Lauf ändert nichts.

1. **Anmelden** über das Tailnet (Phase 8) mit Mail und Passwort.
2. **Firma anlegen** in der Oberfläche: „Firmen“, „+ Firma anlegen“, Name und Kürzel der
   Firma des Legacy-Events (Kürzel = `firma_kurz` unten).
3. **Legacy-Eintrag:** die lokale Datei `laufzeit/legacy-event.json` (0600, nie im Git) mit
   `{"firma_kurz", "name", "beginn", "ende", "ort", "legacy_url"}` (Zeiten ISO 8601,
   `legacy_url` mit `https://`), dann
   `"$PY" -m spaces.event_management.system.legacy eintragen laufzeit/legacy-event.json`.
   Ausgabe nur die Event-ID. Idempotent über `legacy_url`.

- **OK von Felix:** ja (er wählt Mail und Passwort und legt die Firma an).
- **Prüfen:** die Anmeldung gelingt, „Firmen“ zeigt „+ Firma anlegen“; nach dem
  Legacy-Eintrag steht das Festival in der Firma, schreibgeschützt.
- **Abbruch:** Exit ungleich 0 oder eine Ausgabe außer der ID und dem festen Text; die
  Anmeldung scheitert; `legacy eintragen` meldet „Firma nicht gefunden“ (Kürzel prüfen).
- **Rückweg:** `"$PY" -m spaces.event_management.system.betreiber entziehen <person-id>`
  nimmt das Betreiber-Recht und macht offene Sitzungen ungültig; die Person bleibt. Die
  Firma und der Legacy-Eintrag stören leer nicht; löschen ist destruktiv und braucht ein
  eigenes OK.

### Vor `anbinden`: Arbeitsbereich vergleichen

`deploy/anbinden.sh` überschreibt bei **jedem** Lauf `SOUL.md`, `AGENTS.md` und `skills` aller
Arbeitsbereiche. Der Live-Arbeitsbereich der Festival-Gruppe darf dabei nichts verlieren,
was nur dort steht. Darum vorher:

1. `"$PY" deploy/fuellen.py werte.local.json build/` (die gefüllte Saat).
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

### 9. `anbinden`  (`--ich-habe-ok anbinden`)

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

- **Erneut nach `timer`:** Der Bereitstellungs-Arbeiter schreibt `openclaw.json`,
  `anbinden.sh` auch. Darum stoppt `anbinden` einen laufenden `bereitstellung.timer`
  selbst (`sudo systemctl stop bereitstellung.timer`) und bricht ab, solange
  `bereitstellung.service` gerade läuft (dann warten und erneut). Nach T6-a ist der
  Stempel von `timer` danach weg: `--ich-habe-ok timer` startet den Timer wieder.
  `nachrichten.timer` bleibt an.
- **OK von Felix:** ja, nach dem Arbeitsbereich-Vergleich.
- **Abbruch:** Arbeitsbereich unterscheidet sich, `bereitstellung.service` läuft gerade,
  `healthz` des Claw antwortet nicht (60 s), der Shim antwortet nicht.
- **Rückweg:** `--ich-habe-ok zurueck anbinden` spielt die Sicherung der `openclaw.json`
  (Phase 4) in das Volume zurück und startet den Claw neu. Gab es vorher keinen Claw, wird
  er gestoppt; das Volume bleibt. Die in diesem Lauf ausgestellten Schlüssel danach mit
  `… schluessel widerrufen <id>` widerrufen.

### 10. `timer`  (`--ich-habe-ok timer`)

Installiert `bereitstellung` und `nachrichten` (Service + Timer) nach
`/etc/systemd/system` (mit `sudo`), `daemon-reload`, `enable --now` beider Timer.

Gibt es `/etc/event-management/budget-melden.env`, kommt **`budget-melden`** dazu (alle
5 Minuten, `system/budget_melden.py`): Er liest die Zeilen `art=warnung` aus
`laufzeit/shim-budget/` (heute und gestern) und schickt jede genau einmal per Telegram an
Felix; gemerkt wird in `laufzeit/budget-melden.json`, ein gescheiterter Versand wird beim
nächsten Lauf wiederholt. Der Shim bekommt dafür kein Token. Einstellungen (0600, Kopf von
`deploy/budget-melden.service`): `BUDGET_ORDNER`, `BUDGET_ZUSTAND`,
`BUDGET_TELEGRAM_TOKEN_FILE` (Datei 0600 mit dem Bot-Token) und `BUDGET_TELEGRAM_CHAT_ID`.
Welcher Bot und welcher Chat, entscheidet Felix; der Bot muss den Chat anschreiben dürfen
(Felix hat ihm einmal geschrieben). Ohne die Datei bleibt `budget-melden` aus, die Phase
meldet das und besteht trotzdem.

- **OK von Felix:** ja.
- **Abbruch:** ein Timer wird nicht `active`; der erste Lauf scheitert (Journal ansehen,
  meist Python-Umgebung ohne Paket oder falscher Repo-Pfad).
- **Rückweg:** `--ich-habe-ok zurueck timer` (Timer stoppen und deaktivieren, auch
  `budget-melden`, die Unit-Dateien entfernen, `daemon-reload`).

### Brain-Umgebung (nach `timer`)

Die Wahrheitsprüfung `event_change` des Brain fragt den Verlauf über event-mcp ab. Sie
braucht zwei Werte: `EVENT_MCP_URL` (Basis von event-mcp ohne `/mcp`, über den Netzweg unten)
und `EVENT_VERLAUF_TOKEN_FILE` (Pfad zu einer Datei mit dem Schlüssel „Pruefung“ aus
`laufzeit/schluessel/pruefung`, `betreiber_lesen`). Ohne sie meldet die Prüfung „unklar“
statt „ja“ oder „nein“.

Der Brain läuft im vibemind-Swarm-Stack des äußeren Repos. Der Weg dorthin ist der
dokumentierte, nie `docker service update` (räumt den Stack ab) und nie ein
`docker stack deploy` von Hand:

1. **Netzweg** wählen und einrichten (Abschnitt „Netzweg Brain → event-mcp“), inklusive
   `EVENT_MCP_ZUSATZ_HOSTS` und erneutem `dienste`.
2. **Schlüssel als Swarm-Secret,** direkt aus der Datei, ohne Ausgabe:
   `docker secret create event_verlauf_token_v1 <pfad>/pruefung` (die Datei kommt als
   0600-Datei auf den Rechner des Brain, nie per Chat oder Mail; Secrets sind unveränderlich,
   ein neuer Schlüssel bekommt `_v2`). Danach die lokale Kopie löschen.
3. **Stack-Datei** des äußeren Repos (`infra/swarm/vibemind-stack.yml`): bei `brain-core` und
   bei jedem Dienst, der dessen Umgebung spiegelt und Capabilities prüft, unter
   `environment` `EVENT_MCP_URL=<basis>` und
   `EVENT_VERLAUF_TOKEN_FILE=/run/secrets/event_verlauf_token_v1`, unter `secrets` den Namen,
   oben im `secrets:`-Block `event_verlauf_token_v1: {external: true}`. Commit im äußeren
   Repo, keine Werte außer der Basis-URL.
4. **Ausrollen nur über den Launcher** (der macht das Stack-Deploy samt Secrets), mit den
   Betriebsregeln des Launchers (Speicher der Docker-VM vorher messen).

- **OK von Felix:** ja (der Brain startet neu; Schritt 1 und 4 je mit eigenem OK).
- **Prüfen:** eine `event.do`-Probe auf ein Testevent; die Prüfung nennt `verlauf: …`
  statt „EVENT_MCP_URL fehlt“ oder „kein Token“.
- **Abbruch:** event-mcp antwortet mit 421 (Host nicht erlaubt: `EVENT_MCP_ZUSATZ_HOSTS`
  prüfen) oder 401 (Schlüssel falsch oder widerrufen); der Launcher meldet ein fehlendes
  Secret; der Brain wird nicht gesund.
- **Rückweg:** die drei Einträge aus der Stack-Datei nehmen, über den Launcher neu
  ausrollen (die Prüfung fällt auf „unklar“ zurück, `event_app_change` bleibt), den
  Schlüssel mit `… schluessel widerrufen <id>` widerrufen. Das Secret bleibt bis zu einem
  eigenen OK liegen.

## Netzweg Brain → event-mcp (Entscheidung in Teil B)

Der Brain fragt für die Wahrheitsprüfung `event_change` den Verlauf über event-mcp ab
(`aenderungen_seit`, Schlüssel „Pruefung“). event-mcp lauscht auf dem Host nur auf
`127.0.0.1:8171` und lässt per DNS-Rebinding-Schutz nur die Host-Kopfzeilen `127.0.0.1`,
`localhost`, `event-mcp` und `host.docker.internal` zu. Läuft der Brain nicht auf der VM,
braucht es einen Weg dorthin. Felix entscheidet ihn vor dem Schritt „Brain-Umgebung“:

- **`tailscale serve`** für Port 8171 auf einem eigenen Tailnet-Port (wie `tailnet` für
  event-web). Der Brain ruft dann `https://<tailnet-name-der-vm>:<port>`; diese Host-Kopfzeile
  muss erlaubt werden.
- **Tailnet-Port** (Port nur auf der Tailscale-Adresse der VM veröffentlichen). Auch dann
  kommt die Anfrage mit dem Tailnet-Namen oder der Tailnet-Adresse als Host.

In beiden Fällen kommt der Name als `EVENT_MCP_ZUSATZ_HOSTS=<host>[:<port>]` (Komma-Liste)
in `laufzeit/event-app.env`, danach `--ich-habe-ok dienste` erneut (legt event-mcp neu an).
Ein Eintrag ohne Port gilt für jeden Port. Ein ungültiger Eintrag lässt event-mcp nicht
starten; die Meldung nennt nur den Variablennamen. Kein Wert steht im Repo; die Wahl kommt
ohne Werte in den Ledger. Rückweg: Zeile entfernen, `dienste` erneut, `tailscale serve … off`.

## Wer misst was

| Was | Wer |
| --- | --- |
| Vorbedingungen (PG, Images, Dateien, Claim, `.14`, Python-Umgebung) | `ausrollen.sh pruefen`, der Controller liest das Ergebnis |
| Sicherung vollständig (Dump nicht leer, `openclaw.json` da) | `ausrollen.sh sichern` |
| Migrationen angewendet | `db/anwenden.py` (nennt die Dateinamen) |
| Speicher beim Bauen (`MemAvailable`, Swap) | `ausrollen.sh bauen` vorher, der Controller beobachtet währenddessen |
| Gesundheit der Dienste | Proben in `dienste`; Shim `healthy` in `shim` |
| Arbeitsbereich-Vergleich | Controller, Entscheidung bei Felix |
| Schlüssel ausgestellt, nie sichtbar | Controller |
| Freigabe jeder Phase | Felix |

## Offen

- **Zustellung der Budget-Warnung an Felix.** Gebaut (`budget-melden`, Phase `timer`);
  offen ist nur die Wahl von Bot und Chat für `/etc/event-management/budget-melden.env`.
- **Quelle des Shim-Images.** Das bisherige Image stammt aus dem bewerbung-bot-Bestand;
  `shim` baut es nach dem Rezept in Phase 6 aus der Rowboat-Dockerfile (CMD auf
  `0.0.0.0:8125`) neu. Shim und Werkzeugmodul liegen weiter in keinem Repo; bis sie in eines
  wandern, stellt man `EVENT_SHIM_BAUKONTEXT` von Hand zusammen.
- **Ab welchem Rechner der Brain läuft,** entscheidet den Netzweg und wohin die Datei des
  Schlüssels „Pruefung“ geht (Schritt „Brain-Umgebung“).
- **`tailscale serve`-Syntax** gegen die installierte Version prüfen (siehe Phase 8).
- **`pg_dump`-Client** auf dem Host darf nicht älter sein als der Server (PG ≥ 15).
