# Auftragsbuch ausrollen (Plan-Tasks 8 bis 10)

Schrittfolge mit Rückweg. Jede der drei Stufen beginnt erst nach einem ausdrücklichen Ja des Betreibers.
Das Repo ist öffentlich: Diese Anleitung nennt keine Adressen, Namen oder Schlüssel; sie stehen nur auf den
Rechnern selbst. Platzhalter stehen in `<…>`.

Beteiligte Orte:

- **Mini-PC** (kleiner Server): Supabase-PostgreSQL im Docker-Netz `debian_default`, später der Dienst.
- **PC** (Desktop): OpenFang, Gehirn, Wurzel-`.env` des äußeren Repos.
- Der Weg PC → Mini-PC ist ein privates Netz (`tailscale serve` auf Port 8172, wie beim Event-Dienst auf 8171).

Vorab, für alle Stufen: Claim in `002_Koordination_Live.md` (Mini-PC, Supabase, Schema `auftrag`) setzen und
sofort committen; bei fremdem aktivem Claim nichts anfassen und nachfragen.

## Stufe 1 (Plan-Task 8): Migration `100` einspielen

Ja einholen: Es wird eine Migration eingespielt, die zwei Spalten, eine Tabelle, drei Funktionen und eine Rolle
anlegt und einen CHECK erweitert. Sie ist rein zusätzlich.

1. **Sicherung** des Schemas `auftrag` nach dem Muster der bisherigen Sicherungen unter `~/backups/`.
2. **Standard-Rechte prüfen:** In `psql` mit `\ddp` nachsehen, ob Standard-Rechte neuen Tabellen im Schema
   `auftrag` Rechte für `anon`, `authenticated` oder `sales_app` geben. Wenn ja: anhalten und klären, bevor
   eingespielt wird (die neue Tabelle `auftrag.brain_arten` darf diese Rollen nichts geben).
3. **Probe-Lauf auf der echten Datenbank:** `100` und `verify_100` in **einer** Transaktion mit `ROLLBACK`
   (`migration_probe`). Voraussetzung: `096` bis `099` sind eingespielt. Erwartung: kein Fehler. Bei einem Fehler:
   anhalten, nichts einspielen.
4. **Einspielen** von `100_auftragsbuch_brain.sql`. `098` wird danach **nicht** erneut eingespielt: Sein CHECK
   auf `art` lehnt Zeilen des Gehirns ab.
5. **Prüfen:** `SELECT an, art, version FROM auftrag.brain_arten` liefert genau `brain | probe | 1`. Der
   Dienst `auftrag-bote` läuft weiter und sein Herzschlag ist frisch.
6. **Rolle `brain_app`:** Anmeldung und Passwort setzen, das Passwort nur per stdin, nie auf der Kommandozeile
   (Muster `spaces/event_management/deploy/pg_hilfe.py`).

**Rückweg:** `DROP FUNCTION` für die drei Funktionen, `DROP TABLE auftrag.brain_arten`, den alten CHECK
`art IN ('reel', 'video')` wiederherstellen (nur möglich, solange keine Zeile mit `von = 'brain'` existiert),
`DROP ROLE brain_app`. Die zwei Spalten bleiben.

## Stufe 2 (Plan-Task 9): Dienst auf dem Mini-PC starten

Ja einholen: ein neuer Container `auftragsbuch`, eigenes Compose-Projekt, kein anderer Dienst wird neu gestartet.

1. **Code auf den Mini-PC** bringen (Checkout des Zweigs im Ordner des Betreibers); alles Weitere im Ordner
   `spaces/auftragsbuch`.
2. **Schlüssel des Dienstes erzeugen, ohne dass er sichtbar wird:** per Skript erzeugen (z. B.
   `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` in eine Shell-Variable, nie ausgeben). Zwei
   Dinge geschehen mit demselben Wert:
   - auf dem Mini-PC über stdin an `deploy/anbinden.sh`, zusammen mit der DSN von `brain_app`:
     `printf '%s\n%s\n' "$DSN" "$SCHLUESSEL" | bash deploy/anbinden.sh`. Das Skript legt `laufzeit/` (Modus 700)
     an und schreibt `laufzeit/dsn` sowie nur den sha256-Hash nach `laufzeit/schluessel-hash`.
   - **am PC** in die Wurzel-`.env` des äußeren Repos als `AUFTRAGSBUCH_SCHLUESSEL=…`, verdeckt per Skript
     (nicht in den OpenFang-Tresor, nicht in eine Datei im Repo). Dort steht auch `AUFTRAGSBUCH_URL=…` (Stufe 3).
3. **`laufzeit/werte.env`** aus `deploy/werte.beispiel.env` füllen (`AUFTRAGSBUCH_LADEN`,
   `AUFTRAGSBUCH_MANDANT`, `AUFTRAGSBUCH_ZUSATZ_HOSTS` mit dem Namen, unter dem der PC den Dienst aufruft).
4. **Image bauen und starten**, nur in diesem Ordner:
   `export AUFTRAGSBUCH_TAG=<Git-Kurz-SHA>` und `docker compose up -d --build`. Gehören die Dateien in
   `laufzeit/` nicht der UID 1000, vorher `AUFTRAGSBUCH_UID` und `AUFTRAGSBUCH_GID` auf deren Besitzer setzen
   (`id -u`, `id -g`). Der Port ist nur an `127.0.0.1` des Mini-PC gebunden.
5. **Netzweg einrichten** wie in Task 0 gemessen: `tailscale serve` auf Port 8172 zum lokalen Port.
6. **Rauchprobe vom PC** (Adresse und Schlüssel aus der Wurzel-`.env`, nichts davon in die Shell-Historie):
   - `GET /gesund` ohne Schlüssel: HTTP 200.
   - MCP-Weg ohne Schlüssel: HTTP 401.
   - mit Schlüssel `tools/list`: genau `auftrag_probe`, `auftrag_lesen`, `rowboat_vorschlaege_anlegen`,
     `sales_post_pruefen`.
   - `tools/call sales_post_pruefen` mit gültiger Eingabe: Fehler `Unbekannte Auftragsart`, und in
     `auftrag.auftraege` ist keine Zeile entstanden.
   - Ohne Schlüssel in der Prozessliste und ohne ihn im Containerlog: `docker logs auftragsbuch` nennt nur
     Werkzeugname, Ergebnis und Dauer.

**Rückweg:** `docker compose down` im Ordner des Dienstes, den Netzweg entfernen (`tailscale serve` für Port 8172
abschalten). `laufzeit/` kann bleiben oder gelöscht werden; der Schlüssel am PC wird aus der Wurzel-`.env`
entfernt.

## Stufe 3 (Plan-Task 10): OpenFang und Gehirn anschließen

Ja einholen für: Merge `cc/auftragsvertrag` nach `master` (vibemind-os) samt Verweis im äußeren Repo, Merge des
Manifests im Submodul `openfang`, Eintrag des Werkzeug-Servers, Neustart von OpenFang, neues Abbild und
Ausrollen von `brain-core`.

1. **Mergen und Verweise nachziehen**, wie am 21.11. (`docs/superpowers/specs/2026-11-20-zurueck-ideen-bubbles-design.md`,
   7h und 7k). Den Betriebs-Checkout auf den neuen `master` heben.
2. **Eintrag des Werkzeug-Servers (stdio).** OpenFang lädt Werkzeug-Server nur aus
   `openfang/openfang.vibemind.toml`, und diese Datei liegt im öffentlichen Repo. Darum bekommt sie den
   stdio-Eintrag aus `deploy/openfang-eintrag.beispiel.toml`, **ohne** Adresse und ohne Schlüssel. Die
   Platzhalter für Pfade werden am PC eingesetzt. Die Adresse (`AUFTRAGSBUCH_URL`) und der Schlüssel
   (`AUFTRAGSBUCH_SCHLUESSEL`) stehen in der Wurzel-`.env` des PC; `scripts/mcp_mit_env.py` reicht sie der Brücke
   `bruecke.py`, die jeden Aufruf an den Dienst weiterleitet. Dasselbe Muster nutzen `spaces-ideas` und `laura`.
3. **Rechteträger `brain-sales`:** Das Manifest wird zusätzlich **von Hand** nach
   `~/.openfang/agents/brain-sales/agent.toml` gelegt (OpenFang lädt Agenten von dort).
4. **OpenFang neu starten.** Prüfen:
   - `GET /api/mcp/servers` zeigt `auftragsbuch` als verbunden mit vier Werkzeugen;
   - `GET /api/agents` zeigt `brain-sales` mit `mcp_servers_mode: allowlist`;
   - die Werkzeug-Liste im Namen von `brain-sales` enthält genau `auftrag_probe` und `auftrag_lesen`.
5. **Erst danach** `brain-core` mit dem neuen Abbild ausrollen, wie am 21.11. (dieselbe Spec, 7n und 7r).
   Lehre aus dem Zwischenfall dort: nie gleichzeitig mit dem Neustart von OpenFang; erst wenn OpenFang alle
   Werkzeug-Server verbunden hat.

**Rückweg:** Betriebs-Checkout zurück auf `dfc67d54`, altes Abbild `vibemind-brain-core:zurueck-dfc67d54`,
Eintrag des Servers aus der OpenFang-Datei entfernen (und den Rechteträger unter `~/.openfang/agents/`),
OpenFang neu starten.

## Abschluss (Plan-Task 11)

Probe von Anfang bis Ende über das Gehirn: `auftrag_probe`, dann `auftrag_lesen` (`stand == "fertig"`,
`an == "brain"`, `art == "probe"`); Gegenproben: `sales_post_pruefen` ist im Gehirn nicht registriert und bei
OpenFang für `brain-sales` nicht freigeschaltet; bei gestopptem Dienst scheitert der Schritt sofort mit
`auftragsbuch nicht erreichbar`. Danach den Vertrags-Hash aus der Probe mit
`vertrag_hash(lade_auftragsarten(…))` im Betriebs-Checkout vergleichen.
