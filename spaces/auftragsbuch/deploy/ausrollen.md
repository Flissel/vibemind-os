# Auftragsbuch ausrollen (Plan-Tasks 8 bis 11)

Schrittfolge mit Rückweg. Jeder Schritt, der auf dem Mini-PC, an der Datenbank, an OpenFang (Konfiguration,
Agenten-Ordner, Neustart, Nachricht an einen Agenten), am Betriebs-Checkout, am Gehirn (`brain-core`) oder in der
Root-`.env` des PC etwas ändert, startet oder stoppt, und jeder Schritt, der einen Zweig mergt oder pusht, trägt den
Marker **Eigenes Ja des Betreibers:** und braucht sein eigenes Ja. Das gilt auch für jede Aktion eines Rückwegs. Ein
Ja für eine Stufe oder für einen anderen Schritt gilt nicht. Reine Lese- und Prüfschritte tragen keinen Marker; sie
sagen das mit dem Satz „Dieser Schritt liest nur und ändert nichts.“ Ausnahmen davon stehen mit ihrem eigenen Satz
beim Schritt.

Das Repo ist öffentlich: Diese Anleitung nennt keine Adressen, Namen oder Schlüssel; sie stehen nur auf den Rechnern
selbst. Platzhalter stehen in `<…>`. Schlüssel und Passwörter stehen nie als Argument auf einer Kommandozeile, nie in
einer Ausgabe und in keinem Log: Sie entstehen auf dem Rechner, der sie braucht, und werden dort verbraucht, oder sie
reisen in einer Shell-Variable über das eingebaute `printf` in eine Pipe.

Beteiligte Orte:

- **Mini-PC** (kleiner Server): Supabase-PostgreSQL im Docker-Netz `debian_default`, später der Dienst. Der Dienst
  wird in einem eigenen Ordner `~/auftragsbuch-build` gebaut; alle Befehle dieser Anleitung für den Mini-PC laufen in
  `~/auftragsbuch-build/spaces/auftragsbuch`. Unter `~/vibemind-os` liegt dort ein Git-Checkout, aus dem andere
  Dienste laufen (z. B. der Event-Dienst). Dieser Checkout wird nicht angefasst.
- **PC** (Desktop): OpenFang, Gehirn, Root-`.env` des äußeren Repos. Alle Befehle am PC laufen in Git Bash.
- Der Weg PC → Mini-PC ist ein privates Netz: `tailscale serve` veröffentlicht den Dienst als https auf Port 8172
  („tailnet only“), mit Weiterleitung auf `http://127.0.0.1:8172`, wie beim Event-Dienst auf 8171.

Platzhalter:

- `<mini-pc>`: ssh-Ziel des Mini-PC; `<name-des-mini-pc-im-tailnet>`: sein Name im Tailnet, klein geschrieben.
- `<commit>`: der Commit des Zweigs `cc/auftragsvertrag`, der in Stufe 3 gemergt wird; `<Git-Kurz-SHA>` ist seine
  Kurzform. Die Pfade, die auf den Mini-PC kommen, sind darin dieselben wie im gemergten Stand.
- `<ZWEIG-CHECKOUT>`: ein Checkout von vibemind-os am PC auf `<commit>` (z. B. der Arbeitsbaum des Zweigs), nicht der
  Betriebs-Checkout. `<BETRIEBS-CHECKOUT>`: der Checkout von vibemind-os am PC, aus dem OpenFang die Brücke startet.
- `<PFAD-ZUR-VENV>`, `<PFAD-ZUM-AEUSSEREN-REPO>`: wie in `deploy/openfang-eintrag.beispiel.toml`.
- `<db-container>`, `<db-name>`: dieselben Werte wie `AUFTRAGSBUCH_DB_CONTAINER` und `AUFTRAGSBUCH_DB_NAME` in
  `laufzeit/werte.env`. `<psql>` steht für `docker exec -i <db-container> psql -X -v ON_ERROR_STOP=1 -U supabase_admin -d <db-name>`
  auf dem Mini-PC; SQL kommt immer über stdin.

Compose liest `AUFTRAGSBUCH_TAG` bei **jedem** Befehl; ohne Wert bricht schon `down` ab. Darum steht in dieser
Anleitung vor jedem Compose-Befehl die Zuweisung `AUFTRAGSBUCH_TAG=<Tag>`: bei `up` der neue Tag (`<Git-Kurz-SHA>`),
bei allen anderen Befehlen (`ps`, `logs`, `stop`, `start`, `down`) der laufende Tag
(`docker ps --filter name=auftragsbuch --format '{{.Image}}'` zeigt ihn). `AUFTRAGSBUCH_UID` und `AUFTRAGSBUCH_GID`
müssen bei jedem `up` gleich sein; sie stehen deshalb in der nicht versionierten Datei `.env` im Ordner des Dienstes
(Stufe 2, Schritt 2), die Compose bei jedem Befehl selbst liest.

Vorab, für alle Stufen: Claim in `002_Koordination_Live.md` (Mini-PC, Supabase, Schema `auftrag`, OpenFang) setzen
und sofort committen; bei fremdem aktivem Claim nichts anfassen und nachfragen.

## Stufe 1 (Plan-Task 8): Migration `100` einspielen

Die Migration legt zwei Spalten, eine Tabelle, drei Funktionen und die Rolle `brain_app` (`NOLOGIN`, bleibt so) an
und erweitert einen CHECK. Sie ist rein zusätzlich. Voraussetzung: `096` bis `099` sind eingespielt.

1. **Standard-Rechte prüfen:** `printf '%s\n' '\ddp' | <psql>` auf dem Mini-PC. Geben Standard-Rechte neuen
   Tabellen im Schema `auftrag` Rechte für `anon`, `authenticated` oder `sales_app`, anhalten und klären (die Migration
   entzieht sie der neuen Tabelle zwar, aber das gehört vorher besprochen). Dieser Schritt liest nur und ändert nichts.
2. **Sicherung** des Schemas `auftrag` nach dem Muster der bisherigen Sicherungen, z. B.
   `docker exec <db-container> pg_dump -U supabase_admin -d <db-name> -n auftrag > ~/backups/auftrag-vor-100-<datum>.sql`.
   Sie ändert nichts an der Datenbank, schreibt nur eine Sicherungsdatei unter `~/backups/` und ist vom Ja zur Probe
   mit abgedeckt (Schritt 3); dieses Ja wird darum vor der Sicherung eingeholt.
3. **Eigenes Ja des Betreibers:** Probe-Lauf auf der echten Datenbank: `100` und `verify_100` in **einer**
   Transaktion mit `ROLLBACK`, am PC im `<ZWEIG-CHECKOUT>` mit den `SUPABASE_*`-Werten wie bei `096` bis `099`:
   `python -m spaces.marketing.scripts.migration_probe spaces/marketing/db/100_auftragsbuch_brain.sql spaces/marketing/db/verify_100.sql`.
   Sie hinterlässt nichts, nimmt aber für ihre Dauer eine ausschließende Sperre auf `auftrag.auftraege` (wer
   wartet, bricht nach höchstens 5 Sekunden ab: `lock_timeout`). Erwartung: `PROBE OK (zurueckgerollt)`. Bei einem
   Fehler: anhalten, nichts einspielen. Hinweis: `verify_100` legt in der Probe vier Aufträge von `sales` an; ist
   Sales an dem Tag nahe an seiner Grenze von 20, scheitert die Probe aus fremdem Grund, dann später wiederholen.
4. **Eigenes Ja des Betreibers:** `100_auftragsbuch_brain.sql` einspielen, am PC im `<ZWEIG-CHECKOUT>`:
   `ssh <mini-pc> '<psql>' < spaces/marketing/db/100_auftragsbuch_brain.sql`. `098` wird danach **nicht** erneut
   eingespielt: Sein CHECK auf `art` lehnt Zeilen des Gehirns ab.
5. **Prüfen:** `printf '%s\n' 'SELECT an, art, version FROM auftrag.brain_arten;' | <psql>` liefert genau
   `brain | probe | 1`; `printf '%s\n' "SELECT rolcanlogin FROM pg_roles WHERE rolname = 'brain_app';" | <psql>`
   liefert `f`. Der Dienst `auftrag-bote` läuft weiter und sein Herzschlag ist frisch. Dieser Schritt liest nur und
   ändert nichts.

**Rückweg:**

- **Eigenes Ja des Betreibers:** zuerst die Anmelde-Rolle des Dienstes entfernen, falls Stufe 2 sie angelegt hat
  (vorher die Rückwege von Stufe 3 und 2): `printf '%s\n' 'DROP ROLE IF EXISTS auftragsbuch_dienst;' | <psql>`.
- **Eigenes Ja des Betreibers:** die Rechte von `brain_app` entziehen:
  `printf '%s\n' 'REVOKE EXECUTE ON FUNCTION auftrag.brain_anlegen(text, text, int, text, text, jsonb, text, int), auftrag.brain_lesen(uuid), auftrag.brain_probe_erledigen(uuid) FROM brain_app; REVOKE USAGE ON SCHEMA auftrag FROM brain_app;' | <psql>`.
- **Eigenes Ja des Betreibers:** die drei Funktionen entfernen:
  `printf '%s\n' 'DROP FUNCTION auftrag.brain_anlegen(text, text, int, text, text, jsonb, text, int), auftrag.brain_lesen(uuid), auftrag.brain_probe_erledigen(uuid);' | <psql>`.
- **Eigenes Ja des Betreibers:** die Tabelle entfernen: `printf '%s\n' 'DROP TABLE auftrag.brain_arten;' | <psql>`.
- **Normalfall:** Die Zeilen des Gehirns bleiben, und der erweiterte CHECK bleibt. Er ist rein zusätzlich: Für alle
  anderen Absender gilt weiter nur `reel` und `video`, er stört niemanden. Dieser Punkt ändert nichts.
- **Eigenes Ja des Betreibers:** nur statt des Normalfalls: die Proben-Zeilen löschen:
  `printf '%s\n' "DELETE FROM auftrag.auftraege WHERE von = 'brain' AND an = 'brain' AND art = 'probe';" | <psql>`.
- **Eigenes Ja des Betreibers:** nur nach dem Löschen: den alten CHECK setzen (scheitert, solange eine Zeile mit
  `von = 'brain'` existiert):
  `printf '%s\n' "ALTER TABLE auftrag.auftraege DROP CONSTRAINT auftraege_art_chk, ADD CONSTRAINT auftraege_art_chk CHECK (art IN ('reel', 'video'));" | <psql>`.
- **Eigenes Ja des Betreibers:** zuletzt die Rolle: `printf '%s\n' 'DROP ROLE brain_app;' | <psql>`.

Die zwei Spalten bleiben.

## Stufe 2 (Plan-Task 9): Dienst auf dem Mini-PC starten

Neu ist ein Container `auftragsbuch` in einem eigenen Compose-Projekt; kein anderer Dienst wird neu gestartet. Der
Dienst meldet sich an der Datenbank mit der eigenen Anmelde-Rolle `auftragsbuch_dienst` an (Mitglied von
`brain_app`); ihr Passwort entsteht auf dem Mini-PC und verlässt ihn nie.

1. **Eigenes Ja des Betreibers:** Bau-Ordner auf dem Mini-PC füllen, vom PC aus, in einem Git-Checkout, der
   `<commit>` kennt (`git archive` liest nur):
   `git -c core.autocrlf=false archive <commit> spaces/__init__.py spaces/auftragsbuch spaces/_contract | ssh <mini-pc> 'mkdir -p ~/auftragsbuch-build && tar -x -C ~/auftragsbuch-build'`.
   `laufzeit/` und `.env` kommen im Archiv nicht vor; ein späteres erneutes `tar -x` überschreibt sie nicht.
2. **Eigenes Ja des Betreibers:** Einstellungen auf dem Mini-PC anlegen, im Ordner des Dienstes:
   `mkdir -p laufzeit && chmod 700 laufzeit && cp deploy/werte.beispiel.env laufzeit/werte.env`, dann die
   Platzhalter in `laufzeit/werte.env` mit einem Editor ersetzen: `AUFTRAGSBUCH_LADEN`, `AUFTRAGSBUCH_MANDANT`,
   `AUFTRAGSBUCH_ZUSATZ_HOSTS=<name-des-mini-pc-im-tailnet>:8172` (diesen Host-Kopf sieht der Dienst hinter
   `tailscale serve`), `AUFTRAGSBUCH_DB_CONTAINER` (`docker ps` zeigt ihn), `AUFTRAGSBUCH_DB_HOST` (Name der
   Datenbank im Netz `debian_default`) und `AUFTRAGSBUCH_DB_NAME`. Dazu die Datei für Compose:
   `printf 'AUFTRAGSBUCH_UID=%s\nAUFTRAGSBUCH_GID=%s\n' "$(id -u)" "$(id -g)" > .env` (als derselbe Benutzer, der
   die folgenden Skripte ausführt; ihm gehören die Dateien in `laufzeit/`).
3. **Eigenes Ja des Betreibers:** Datenbank-Zugang auf dem Mini-PC: `bash deploy/db_zugang.sh`. Das Skript erzeugt
   ein Passwort nur in einer Shell-Variable, legt die Rolle `auftragsbuch_dienst` an (`LOGIN`, Mitglied von
   `brain_app`, sonst keine Rechte) oder setzt ihr Passwort neu, und schreibt `laufzeit/dsn` (Modus 600). psql sieht
   nur den SCRAM-Verifier, nie das Passwort. Wird es wiederholt, während der Dienst läuft, gilt das neue Passwort erst
   nach einem Neustart des Dienstes (eigenes Ja). Prüfen:
   `printf '%s\n' "SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname IN ('brain_app', 'auftragsbuch_dienst') ORDER BY 1;" | <psql>`
   liefert `auftragsbuch_dienst | t` und `brain_app | f`.
4. **Eigenes Ja des Betreibers:** Schlüssel des Dienstes: Er entsteht am PC und wird nie angezeigt; auf den Mini-PC
   kommt nur sein Hash. In Git Bash am PC:
   `SCHLUESSEL=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')`, dann
   `printf '%s\n' "$SCHLUESSEL" | ssh <mini-pc> 'bash ~/auftragsbuch-build/spaces/auftragsbuch/deploy/anbinden.sh'`.
   `anbinden.sh` nimmt genau diese eine Zeile, schreibt nur `laufzeit/schluessel-hash` und bricht ab, wenn
   `laufzeit/dsn` fehlt. Diese Shell bleibt bis Schritt 8 offen (der Schlüssel steht nur in ihrer Variable). Geht sie
   verloren, Schritt 4 wiederholen; läuft der Dienst dann schon, braucht er danach einen Neustart (eigenes Ja).
5. **Eigenes Ja des Betreibers:** Image bauen und starten, im Ordner des Dienstes auf dem Mini-PC:
   `AUFTRAGSBUCH_TAG=<Git-Kurz-SHA> docker compose up -d --build`. `AUFTRAGSBUCH_UID`/`AUFTRAGSBUCH_GID` kommen aus
   `.env` (Schritt 2). Der Port ist nur an `127.0.0.1` des Mini-PC gebunden. Endet der Start mit
   `auftragsbuch: Datenbank nicht erreichbar (<Typ>)` oder `auftragsbuch: Vertrag nicht ladbar (<Typ>)` (in
   `AUFTRAGSBUCH_TAG=<Git-Kurz-SHA> docker compose logs auftragsbuch`), anhalten.
6. **Rauchprobe auf dem Mini-PC** gegen `127.0.0.1` (dort braucht es keinen Schlüssel):
   `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8172/gesund` liefert `200`;
   `curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' http://127.0.0.1:8172/mcp`
   liefert `401`; `AUFTRAGSBUCH_TAG=<laufender Tag> docker compose ps` zeigt den Dienst als `healthy`. Dieser Schritt
   liest nur und ändert nichts.
7. **Eigenes Ja des Betreibers:** Netzweg einrichten, auf dem Mini-PC:
   `tailscale serve --bg --https=8172 http://127.0.0.1:8172` (wie beim Event-Dienst, ggf. mit `sudo`). Nie
   `tailscale serve reset`: Das nähme auch alle anderen Freigaben weg. Danach zeigt `tailscale serve status` 8172
   neben den bisherigen Freigaben, auch die des Event-Dienstes.
8. **Eigenes Ja des Betreibers:** Adresse, Schlüssel und Zertifikatsliste in die Root-`.env` des PC, in der Shell
   aus Schritt 4:
   `printf 'AUFTRAGSBUCH_URL=%s\nAUFTRAGSBUCH_SCHLUESSEL=%s\nAUFTRAGSBUCH_CA_DATEI=<PFAD-ZUR-VENV>/Lib/site-packages/certifi/cacert.pem\n' 'https://<name-des-mini-pc-im-tailnet>:8172/mcp' "$SCHLUESSEL" | "<PFAD-ZUR-VENV>/Scripts/python.exe" "<ZWEIG-CHECKOUT>/spaces/auftragsbuch/deploy/pc_env_setzen.py" "<PFAD-ZUM-AEUSSEREN-REPO>/.env"`,
   danach `unset SCHLUESSEL`. Die Adresse endet genau auf `/mcp`, ohne Schrägstrich (mit Schrägstrich antwortet der
   Dienst mit 307, die Brücke meldet dann `unerwartete Antwort (307)`). `pc_env_setzen.py` ersetzt vorhandene Zeilen
   statt doppelt anzuhängen und gibt keinen Wert aus. Die Root-`.env` ist die gewählte Ablage: Jeder Prozess, der
   diese Datei lesen kann, hält Adresse und Schlüssel des Dienstes. Die dritte Zeile, `AUFTRAGSBUCH_CA_DATEI`, ist
   kein Geheimnis, sondern der Pfad zur Zertifikatsliste (`cacert.pem`) im Paket `certifi` der Venv: Der Zertifikatsspeicher von
   Windows kann abgelaufene Zwischenzertifikate halten, die die TLS-Bibliothek dieses Pythons dann wählt (gemessen am
   22.11.), und die Brücke prüft dann nur gegen diese Liste. Fehlt die Zeile, meldet die Rauchprobe
   `Zertifikat nicht pruefbar`.
9. **Rauchprobe vom PC** über die echte Brücke, Adresse und Schlüssel aus der Root-`.env`:
   `"<PFAD-ZUR-VENV>/Scripts/python.exe" "<PFAD-ZUM-AEUSSEREN-REPO>/scripts/mcp_mit_env.py" --env AUFTRAGSBUCH_URL --env AUFTRAGSBUCH_SCHLUESSEL --env AUFTRAGSBUCH_CA_DATEI -- "<ZWEIG-CHECKOUT>/spaces/auftragsbuch/deploy/rauchprobe.py"`.
   Sie legt nichts an: `initialize`, `tools/list` und der `tools/call` von `sales_post_pruefen`, der mit
   `Unbekannte Auftragsart` scheitern muss, schreiben keine Zeile. Erwartung: drei Zeilen `ok …`
   (`initialize`, `tools/list (vier Werkzeuge)`, `tools/call sales_post_pruefen (Unbekannte Auftragsart)`). Danach
   auf dem Mini-PC `printf '%s\n' "SELECT count(*) FROM auftrag.auftraege WHERE von = 'brain' AND art <> 'probe';" | <psql>`
   liefert `0`, und `AUFTRAGSBUCH_TAG=<laufender Tag> docker compose logs auftragsbuch` nennt nur Werkzeugname,
   Ergebnis und Dauer.

**Rückweg:**

- **Eigenes Ja des Betreibers:** Adresse, Schlüssel und Zertifikatsliste aus der Root-`.env` des PC entfernen
  (vorher den Rückweg von Stufe 3, falls ausgeführt):
  `"<PFAD-ZUR-VENV>/Scripts/python.exe" "<ZWEIG-CHECKOUT>/spaces/auftragsbuch/deploy/pc_env_setzen.py" "<PFAD-ZUM-AEUSSEREN-REPO>/.env" --entfernen AUFTRAGSBUCH_URL AUFTRAGSBUCH_SCHLUESSEL AUFTRAGSBUCH_CA_DATEI`.
- **Eigenes Ja des Betreibers:** den Netzweg abschalten, auf dem Mini-PC: `tailscale serve --https=8172 off` (nur
  diese Freigabe).
- **Eigenes Ja des Betreibers:** `AUFTRAGSBUCH_TAG=<laufender Tag> docker compose down` im Ordner des Dienstes.
- **Eigenes Ja des Betreibers:** den Bau-Ordner samt Einstellungen löschen (`rm -r ~/auftragsbuch-build`), oder ihn
  bleiben lassen: Ohne Container liest ihn niemand. Die Anmelde-Rolle geht mit dem Rückweg von Stufe 1.

## Stufe 3 (Plan-Task 10): OpenFang und Gehirn anschließen

Jede der folgenden Handlungen braucht ihr eigenes Ja, jeweils vor dem Schritt, in dem sie steht. Der Eintrag des
Werkzeug-Servers `auftragsbuch` steht seit Task 7 in `openfang/openfang.vibemind.toml` (stdio, startet die Brücke
über `scripts/mcp_mit_env.py`, ohne Adresse und ohne Schlüssel); er wird nicht noch einmal eingetragen.

1. **Ausgangsstand festhalten**, am PC: `git -C <BETRIEBS-CHECKOUT> rev-parse --short HEAD` (notieren als
   `<AUSGANG-CHECKOUT>`), `git -C <BETRIEBS-CHECKOUT>/openfang rev-parse --short HEAD` (`<AUSGANG-OPENFANG>`),
   `docker service inspect vibemind_brain-core --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}'`
   (`<AUSGANG-ABBILD>`), dazu im äußeren Repo der Vertragstest
   `python -m pytest tests/integration/test_openfang_mcp_authority_contract_v1.py -q` (Ausgangswert: 4 rot, 14 grün)
   und `grep -c '^AUFTRAGSBUCH_\(URL\|SCHLUESSEL\|CA_DATEI\)=' "<PFAD-ZUM-AEUSSEREN-REPO>/.env"` (muss `3` liefern,
   sonst zuerst Stufe 2, Schritt 8). Dieser Schritt liest nur und ändert nichts.
2. **Eigenes Ja des Betreibers:** openfang mergen und pushen: im Submodul `openfang` des `<ZWEIG-CHECKOUT>`
   `cc/auftragsvertrag` nach `main` (Fast-Forward, sonst ein echter Merge; kein Squash, kein Rebase, damit der
   Verweis von vibemind-os erreichbar bleibt), dann `git push origin main`. Prüfen:
   `git -C <ZWEIG-CHECKOUT>/openfang branch -r --contains "$(git -C <ZWEIG-CHECKOUT> rev-parse <commit>:openfang)"`
   zeigt `origin/main`.
3. **Eigenes Ja des Betreibers:** vibemind-os mergen und pushen, erst nach Schritt 2: `cc/auftragsvertrag` nach
   `master`, wie am 21.11. (`docs/superpowers/specs/2026-11-20-zurueck-ideen-bubbles-design.md`, 7h: läuft `master`
   inzwischen weiter, ihn erst einmergen, Verweis `openfang` auf den gemergten Stand von `main` nachziehen, Tests auf
   dem gemergten Stand), dann `git push origin master`.
4. **Eigenes Ja des Betreibers:** im äußeren Repo den Verweis auf vibemind-os nachziehen (wie 7h), committen und
   pushen.
5. **Eigenes Ja des Betreibers:** Betriebs-Checkout heben, wie am 21.11. (dieselbe Spec, 7k):
   `git -C <BETRIEBS-CHECKOUT> fetch origin`, `git -C <BETRIEBS-CHECKOUT> checkout --detach origin/master`,
   `git -C <BETRIEBS-CHECKOUT> submodule update openfang` (nur dieses Submodul). Damit wird der Eintrag
   `auftragsbuch` in der wirksamen Datei beim nächsten Neustart von OpenFang wirksam, auch bei einem Neustart durch
   den Wächter. Voraussetzung: `AUFTRAGSBUCH_URL`, `AUFTRAGSBUCH_SCHLUESSEL` und `AUFTRAGSBUCH_CA_DATEI` stehen in der
   Root-`.env` (Schritt 1).
6. **Prüfen** nach dem Heben: `grep -c '^name = "auftragsbuch"$' <BETRIEBS-CHECKOUT>/openfang/openfang.vibemind.toml`
   liefert genau `1`; der Vertragstest aus Schritt 1 erneut, verglichen mit dem Ausgangswert 4 rot / 14 grün (ein
   neuer roter Test ist ein Befund für den Betreiber, der Vertrag dort wird nicht stillschweigend geändert). Dieser
   Schritt liest nur und ändert nichts.
7. **Eigenes Ja des Betreibers:** Rechteträger `brain-sales` in den Agenten-Ordner von OpenFang kopieren, und zwar
   **unverändert**, mit dem Anbieter, wie der Generator ihn erzeugt hat:
   `mkdir -p ~/.openfang/agents/brain-sales && cp <BETRIEBS-CHECKOUT>/openfang/agents/brain-sales/agent.toml ~/.openfang/agents/brain-sales/agent.toml`,
   danach `cmp` beider Dateien ohne Ausgabe. Den Anbieter nicht umstellen (nicht auf `claude-code` wie bei
   `brain-ideas`): Dieser Treiber startete eine Claude-CLI-Sitzung am PC, vor der nur ein Prompt steht.
8. **Eigenes Ja des Betreibers:** OpenFang neu starten (nicht gleichzeitig mit `brain-core`).
9. **Prüfen** nach dem Neustart, über die OpenFang-API bzw. das Dashboard. Dieser Schritt liest nur und ändert
   nichts.
   - `GET /api/mcp/servers` zeigt `auftragsbuch` als verbunden mit vier Werkzeugen. Wenn nicht: anhalten, die
     Ursache beheben (meist fehlt ein Wert in der Root-`.env`), dann zehn Minuten lang alle zwei Minuten nachsehen
     und notieren, ob OpenFang einen stdio-Server, dessen Start fehlschlug, von selbst neu verbindet. Belegt ist
     das nicht; bis dahin braucht es einen erneuten Neustart (eigenes Ja).
   - `GET /api/agents` zeigt `brain-sales` mit `mcp_servers_mode: allowlist`; seine Werkzeug-Liste enthält genau
     `mcp_auftragsbuch_auftrag_probe` und `mcp_auftragsbuch_auftrag_lesen`.
   - Welche Agenten führen ein Werkzeug `mcp_auftragsbuch_*`? Am PC, der Schlüssel von OpenFang kommt aus der
     Root-`.env` und steht nie auf der Kommandozeile:
     `"<PFAD-ZUR-VENV>/Scripts/python.exe" "<PFAD-ZUM-AEUSSEREN-REPO>/scripts/mcp_mit_env.py" --env OPENFANG_API_KEY --set OPENFANG_URL=http://127.0.0.1:4200 -- "<ZWEIG-CHECKOUT>/spaces/auftragsbuch/deploy/openfang_aufruf.py" werkzeuge-je-agent auftragsbuch`.
     Das Hilfsprogramm holt die Agentenliste und je Agent dessen Detail (die Liste trägt keine Werkzeuge) und gibt
     zwei Gruppen aus. Gruppe 1, ausdrücklich: Agenten mit einem Werkzeug `mcp_auftragsbuch_*` in
     `capabilities.tools`, je Zeile Name und diese Werkzeuge (gleichnamige Agenten stehen beide da, mit
     `(Kennung …)` am Namen). Erwartet ist genau eine Zeile
     `brain-sales: mcp_auftragsbuch_auftrag_lesen, mcp_auftragsbuch_auftrag_probe`; nur dann Exit 0, jeder weitere
     Agent oder eine abweichende Werkzeugmenge ergibt Exit 1. Gruppe 2 unter der Überschrift
     `offener Zugang (sieht alle Werkzeuge des Servers):`: Agenten mit leerer oder `*`-Werkzeugliste, die den Server
     sehen (`mcp_servers_mode` `all`, `auftragsbuch` in `mcp_servers` oder `mcp_servers` leer), je Zeile nur der
     Name. Sie wird immer ausgegeben und ändert den Exit-Code nicht (bekannte Lücke, siehe „Offene Punkte für
     Stufe 2“); jeder dort genannte Agent wird notiert und dem Betreiber gemeldet. Scheitert ein Detail-Abruf,
     endet der Befehl mit Exit 1 und `unerwartet (<Status>)`. Ein Agent mit leerem `mcp_servers` sieht alle Werkzeug-Server, mit
     leerem `capabilities.tools` auch alle ihre Werkzeuge; nach dem Repo (Test
     `tests/test_openfang_offene_mcp_zugaenge.py`) gilt das für `capability-gap-filer` und
     `learning-course-factory`, sofern sie im Betrieb geladen sind. Die Sperre je Auftragsart in OpenFang gilt nur
     für die Identität `brain-sales`, und wer den Schlüssel von OpenFang hat, kann jede registrierte Identität
     wählen. In Stufe 1 hält die Datenbank echte Aufträge auf (`Unbekannte Auftragsart`); Proben (höchstens 50 am
     Tag) wären für solche Agenten aber möglich.
10. **Eigenes Ja des Betreibers:** eine Nachricht an `brain-sales` über OpenFang schicken (beliebiger Text).
    Erwartung: ein Fehler oder die feste Ablehnung `brain-sales accepts no messages.`, nie ein Werkzeug-Aufruf (in
    `auftrag.auftraege` keine neue Zeile). Das Ergebnis wird notiert.
11. **Eigenes Ja des Betreibers:** `brain-core` mit dem neuen Abbild ausrollen, erst nachdem OpenFang alle
    Werkzeug-Server verbunden hat, auf dem Weg vom 21.11. (dieselbe Spec, 7n und 7r; Lehre aus dem Zwischenfall
    dort: nie gleichzeitig mit dem Neustart von OpenFang, und nur mit genug freiem Speicher).

Der Rückweg arbeitet mit den in Schritt 1 notierten Werten, nie mit festen.

**Rückweg:**

- **Eigenes Ja des Betreibers:** `brain-core` mit `<AUSGANG-ABBILD>` ausrollen, auf dem Weg vom 21.11. (7n, 7r),
  nicht gleichzeitig mit einem Neustart von OpenFang.
- **Eigenes Ja des Betreibers:** den Betriebs-Checkout zurücksetzen: `git -C <BETRIEBS-CHECKOUT> checkout --detach <AUSGANG-CHECKOUT>`.
- **Eigenes Ja des Betreibers:** das Submodul dort zurücksetzen:
  `git -C <BETRIEBS-CHECKOUT>/openfang checkout --detach <AUSGANG-OPENFANG>`. Damit fehlt der Eintrag
  `auftragsbuch` beim nächsten Neustart wieder.
- **Eigenes Ja des Betreibers:** den Rechteträger aus dem Agenten-Ordner entfernen: `rm -r ~/.openfang/agents/brain-sales`.
- **Eigenes Ja des Betreibers:** OpenFang neu starten (auch der Rückweg-Neustart braucht es).
- **Prüfen:** `GET /api/mcp/servers` ohne `auftragsbuch`, `GET /api/agents` ohne `brain-sales`. Nach dem Code stellt
  OpenFang Agenten aus seiner eigenen Datenbank wieder her, auch ohne `agent.toml`; dann steht `brain-sales` nach dem
  Neustart noch da. Dieser Schritt liest nur und ändert nichts.
- **Eigenes Ja des Betreibers:** nur wenn `brain-sales` noch da ist: ihn über die OpenFang-API abmelden. Das
  Hilfsprogramm löst die Kennung selbst auf und sendet `DELETE /api/agents/<kennung>`; es akzeptiert nur den Namen
  `brain-sales`: `"<PFAD-ZUR-VENV>/Scripts/python.exe" "<PFAD-ZUM-AEUSSEREN-REPO>/scripts/mcp_mit_env.py" --env OPENFANG_API_KEY --set OPENFANG_URL=http://127.0.0.1:4200 -- "<ZWEIG-CHECKOUT>/spaces/auftragsbuch/deploy/openfang_aufruf.py" agent-entfernen brain-sales`. Ausgabe `entfernt` oder `nicht vorhanden` (je Exit 0),
  sonst `unerwartet (<Status>)` (Exit 1). Danach erneut prüfen.

Die Merges auf `main`, `master` und im äußeren Repo bleiben; sie zurückzunehmen wäre ein eigener Schritt mit eigenem
Ja und ist hier nicht vorgesehen.

## Abschluss (Plan-Task 11)

Probe und Lesen sind zwei getrennte Pläne: Das Gehirn legt die Antwort eines Werkzeugs ungeparst ab
(`content[0].text`), ein Platzhalter `{{state.probe.auftrag_id}}` bliebe leer.

1. **Eigenes Ja des Betreibers:** Plan 1, Probe über das Gehirn: `POST /api/multihop/execute` mit genau einem
   Schritt ohne Absicht: `step_id: probe`, `execution_target: mcp:brain-sales:auftragsbuch:auftrag_probe`,
   `description` (Pflichtfeld, beliebiger Text), `arg_template: ""` (leerer Text, kein `{}`: das Gehirn macht aus
   einem leeren Objekt den Text `{}` und reicht ihn als Argument `value` weiter, das die geschlossene Eingabe des
   Werkzeugs ablehnt), `output_var: probe`. Erwartung: Schritt `ok`; aus dem abgelegten Ergebnis (JSON-Text) die
   Kennung `auftrag_id` und den `vertrag_hash` notieren.
2. **Eigenes Ja des Betreibers:** Plan 2, Lesen: ein Schritt `step_id: lesen`, `description`,
   `execution_target: mcp:brain-sales:auftragsbuch:auftrag_lesen`, `arg_kwarg: id`,
   `arg_template: "<auftrag_id aus Plan 1>"` (die Kennung von Hand eingesetzt; ohne `arg_kwarg` käme sie als
   `value` an). Erwartung: Schritt `ok`; `stand == "fertig"`, `an == "brain"`, `art == "probe"`.
   So gelaufen am 22.11.2026: Plan 1 in 0,9 s, Plan 2 in 1,1 s. Die Belege zu beiden Schritten liefert
   `GET /api/runtime/receipts/<invocation_id>` von OpenFang (`invocation_id` =
   `brain-mcp-` + die ersten 32 Zeichen von sha256 über `<plan_id>\n<plan_revision>\n<step_id>`); das allgemeine
   Protokoll `/api/audit/recent` führt diese Aufrufe nicht.
3. **Belege nachlesen**, unabhängig vom Gehirn: in `auftrag.auftraege` genau eine neue Zeile `von = brain, an = brain,
   art = probe, stand = fertig`; in der OpenFang-Datenbank zu beiden Schritten eine Freigabe und ein Beleg im Namen
   von `brain-sales`. Dieser Schritt liest nur und ändert nichts.
4. **Eigenes Ja des Betreibers:** Gegenprobe im Gehirn: ein Plan wie in Schritt 1, dessen einziger Schritt
   `execution_target: mcp:brain-sales:auftragsbuch:sales_post_pruefen` nennt. Erwartung: Er scheitert im Gehirn,
   weil das Ziel nicht registriert ist; an OpenFang wird nichts gesendet, in `auftrag.auftraege` entsteht keine
   Zeile.
5. **Eigenes Ja des Betreibers:** Gegenprobe direkt an OpenFang: derselbe Aufruf von `sales_post_pruefen` im Namen
   von `brain-sales` (das Hilfsprogramm löst die Kennung selbst auf; Schlüssel aus der Root-`.env`, nie auf der
   Kommandozeile): `"<PFAD-ZUR-VENV>/Scripts/python.exe" "<PFAD-ZUM-AEUSSEREN-REPO>/scripts/mcp_mit_env.py" --env OPENFANG_API_KEY --set OPENFANG_URL=http://127.0.0.1:4200 -- "<ZWEIG-CHECKOUT>/spaces/auftragsbuch/deploy/openfang_aufruf.py" gegenprobe brain-sales sales_post_pruefen`.
   Das Hilfsprogramm prüft vorab über `GET /api/mcp/servers`, dass der Server `auftragsbuch` verbunden ist und das
   Werkzeug führt (sonst `unerwartet (Server auftragsbuch nicht verbunden)` bzw.
   `unerwartet (Werkzeug am Server nicht vorhanden)`, ohne Aufruf): Ohne verbundenen Server fehlte das Werkzeug
   überall und die Antwort sähe wie eine Ablehnung aus. Als Ablehnung zählt nur der JSON-RPC-Fehler `-32602` mit der
   Nachricht `Tool not permitted for caller agent: mcp_auftragsbuch_sales_post_pruefen`.
   Erwartung: `abgelehnt von OpenFang (erwartet)` und Exit 0, weil das Werkzeug für `brain-sales` nicht
   freigeschaltet ist; in `auftrag.auftraege` entsteht keine Zeile. Kommt stattdessen
   `DURCHGELASSEN: Unbekannte Auftragsart - anhalten` (Exit 1), hat OpenFang durchgelassen (erst die Datenbank hat
   abgelehnt); kommt `DURCHGELASSEN: Recht vorhanden, nur die Freigabe fehlte - anhalten` (Exit 1), war das Recht da
   und nur die Freigabe fehlte, die Sperre hat nicht gehalten: in beiden Fällen anhalten und melden.
   `unerwartet (<Grund>)` (Exit 1), auch `unerwartet (JSON-RPC-Fehler <Code>)` und jeder HTTP-Fehler, ist ebenfalls
   ein Befund für den Betreiber, keine Ablehnung.
6. **Eigenes Ja des Betreibers:** Dienst stoppen, auf dem Mini-PC: `AUFTRAGSBUCH_TAG=<laufender Tag> docker compose stop`.
   Dann Plan 1 erneut: Der Schritt scheitert sofort mit einer Meldung, die den Server nennt, hinter `tailscale serve`
   eher `auftragsbuch: unerwartete Antwort (502)`, sonst `auftragsbuch nicht erreichbar`; kein anderer Weg wird
   versucht.
7. **Eigenes Ja des Betreibers:** Dienst wieder starten: `AUFTRAGSBUCH_TAG=<laufender Tag> docker compose start`.
   Prüfen: Die beiden `curl` aus Stufe 2, Schritt 6 liefern wieder `200` und `401`, und die Rauchprobe aus Stufe 2,
   Schritt 9 (jetzt mit `<BETRIEBS-CHECKOUT>` statt `<ZWEIG-CHECKOUT>`) liefert wieder drei Zeilen `ok`.
8. **Vertrags-Hash vergleichen:** den `vertrag_hash` aus Plan 1 mit
   `python -c "from pathlib import Path; from spaces.auftragsbuch.vertrag import lade_auftragsarten, vertrag_hash; print(vertrag_hash(lade_auftragsarten(Path('spaces/_contract'))))"`
   im `<BETRIEBS-CHECKOUT>`. Dieser Schritt liest nur und ändert nichts.

Danach den Claim in `002_Koordination_Live.md` schließen (Plan-Task 11, Step 5).

## Offene Punkte für Stufe 2

- Herkunft erzwingen (Spec 8.3), bevor die erste echte Auftragsart freigeschaltet wird.
- Kein anderer OpenFang-Agent sieht `mcp_auftragsbuch_*`: eine harte Sperre statt der Prüfung in Stufe 3, Schritt 9.
- Rechteträger ohne startbares Modell (heute trägt `brain-sales` Modell, Ablehnungs-Prompt und 2000 Tokens je Stunde).
- Das Gehirn weigert sich, einem Rechteträger eine Nachricht zu schicken.
- `auftrag.naechster(p_an)` kennt keine Arten: Vor einer `brain_arten`-Zeile für einen Empfänger braucht es dort eine
  Sperre.
- Rechteträger `brain-rowboat`.
- Übergänge `in_arbeit → fertig | fehler` für Empfänger.
- `mcp_discovery` des Gehirns startet die Brücke beim Hochfahren selbst.
