# Eingabefenster — Entwurf

**Ziel in einem Satz.** Ein Geheimnis soll den Kontext des Agenten nie
betreten: der Agent nennt einen Einmal-Link, der Mensch trägt den Wert in
seinem eigenen Browser ein, und der Sidecar nimmt ihn direkt entgegen — der
Agent sieht nur Zeiger und Zustand.

**Stand, auf dem das aufsetzt.** Die Spec `2026-09-08-plugin-setup-agent.md`
hat fünf Lücken gemessen; vier sind geschlossen und live bewiesen (OpenFang
nimmt über HTTP entgegen, die Ausgabeliste wächst ohne Neustart, Rowboat
nennt die richtigen Namen, die Bind-Route existiert). Offen ist Lücke 5:
*„Kein Ort, an dem ein Fenster den Wert entgegennimmt."* Dieser Entwurf
schließt sie — und korrigiert dabei eine Drift: gebaut wurde nicht ein
Fenster, das entgegennimmt, sondern ein Agent, der durchreicht.

---

## Was heute falsch ist (gemessen am 11./12.09.2026)

| # | Befund | Beleg |
| --- | --- | --- |
| 1 | Der Agent hält den Wert in der Hand | `schluessel_entgegennehmen(projekt, plugin, referenz, art, wert, ziel)` — `wert` ist ein Parameter, den der Aufrufer übergibt |
| 2 | Die Eindämmung aus D1 gilt nicht | Auf der Subscription-Bahn hatte der Agent `Read`, `Write`, `ToolSearch`, `Workflow`, `Skill`, `SendMessage` und `mcp__captain__*` — Claude Codes vollen Satz. Die vier Werkzeuge des Space sind die *zusätzlichen*, nicht die Grenze |
| 3 | Ein sichtbares Fenster ist im Container nicht herstellbar | `DISPLAY` ist im Container nicht gesetzt; `marketing-claw` fährt denselben Weg headless |
| 4 | Das native Fenster zu teilen kostet eine CDP-Tür | openclaws `BrowserProfileConfig` kennt `cdpUrl`/`attachOnly`/`driver`; CDP ist unauthentifiziert und gibt Vollzugriff auf den Browser samt aller angemeldeten Sitzungen |
| 5 | Ein Fehlschlag ist terminal | Nach `fehlgeschlagen` steht die Zeile samt Vault-Kopie; ein zweiter Versuch derselben Referenz kollidiert am `UNIQUE`-Index |

Vorhanden und wiederverwendbar: der **OAuth-Provisioner**
(`spaces/rowboat/rowboat/scripts/provision-oauth-token.py`) — er macht
RFC-9728-Discovery, dynamische Client-Registrierung (keine vorab registrierte
App nötig), Authorization-Code mit PKCE, den lokalen Callback und den
Token-Tausch, und gibt den Wert laut eigener Doku nie heraus außer in die
`--out`-Datei. Ebenso die gesamte bewiesene Kette Aufnahme → Verifikation →
Übergabe → Kopie löschen samt 81 Tests und `E2E-PROOF.md` Teil V.

---

## Der Weg eines Schlüssels, neu

```text
  Mensch              Agent            Sidecar (Host)      OpenFang
    |                   |                   |                  |
    | "richte github    |                   |                  |
    |  ein"             |                   |                  |
    |------------------>|                   |                  |
    |                   | eingabe_anfordern |                  |
    |                   |------------------>| Anfrage im       |
    |                   |<------------------| Speicher, Token  |
    |  "oeffne <link>"  | {url, ablauf_iso} |                  |
    |<------------------|                   |                  |
    |                                       |                  |
    |  eigener Browser: Wert eintragen      |                  |
    |  (oder beim Anbieter anmelden)        |                  |
    |-------------------------------------->| POST, Wert       |
    |                                       |  NUR hier        |
    |                                       |-- ablegen        |
    |                                       |-- VERIFIZIEREN ->X
    |                                       |-- uebergeben --->|
    |                                       |-- Kopie loeschen |
    |                   | einrichtung_status|                  |
    |                   |------------------>|                  |
    |                   |<------------------| {zustand,hinweis}|
    |  "uebernommen"    |  nie ein Wert     |                  |
    |<------------------|                   |                  |
```

Der Wert existiert genau auf einer Strecke: vom Browser des Menschen in den
Sidecar. Er berührt den Agenten nicht, weder hin noch zurück.

---

## Entscheidungen

### E1 — Der Agent bekommt einen Zeiger, nie einen Wert

Zwei Werkzeuge treten an die Stelle des einen alten:

- `eingabe_anfordern(projekt, plugin, referenz, art, ziel)`
  → `{ok, url, ablauf_iso}` — legt eine schwebende Anfrage an und gibt einen
  Einmal-Link zurück; `ablauf_iso` ist der Verfallszeitpunkt als
  ISO-8601-Zeitstempel, damit der Agent dem Menschen sagen kann, wie lange
  der Link gilt.
- `einrichtung_status(referenz)` → `{ok, zustand, hinweis}` — `zustand` ist
  einer aus `angefordert`, `entgegengenommen`, `verifiziert`, `uebernommen`,
  `fehlgeschlagen`; `hinweis` traegt bei Fehlschlag den Statuscode des
  Anbieters als Text. Nie einen Wert, nie einen Antwortkörper (D3 der
  Vorgänger-Spec, unverändert).

  Das Feld heisst bewusst `zustand` und nicht `status`: in den bestehenden
  Werkzeugen dieses Space bedeutet `status` durchweg einen HTTP-Statuscode.
  Dasselbe Wort für zwei Dinge in derselben Werkzeugfamilie waere eine
  Verwechslung mit Ansage.

Der Einmal-Link darf im Transkript landen. Nach Gebrauch oder Ablauf ist er
wertlos; das ist der Unterschied zu einem Geheimnis.

### E2 — `schluessel_entgegennehmen` bleibt, verlässt aber die Werkzeugliste

Die Funktion ist der interne Schreibweg des Formulars und behält ihren
Vertrag, ihre Tests und ihre Deckung durch `E2E-PROOF.md` Teil V. Sie wird
nur nicht mehr als MCP-Werkzeug registriert. Damit ist der Agent strukturell
außen vor, ohne dass die bewiesene Kette wegfällt.

Ersatzlos entfernen wäre nicht strenger — die Zusicherung kommt daher, dass
das Werkzeug nicht in der Liste steht, nicht daher, dass es die Funktion
nicht gibt — würde aber den Live-Beweis kosten.

### E3 — Das Formular serviert derselbe Prozess, auf demselben Port

Das Formular kommt als eigene Route neben `/mcp`, im selben Prozess.
Begründung: eine schwebende Anfrage ist Prozesszustand, und zwei Prozesse
bräuchten dafür einen geteilten Speicher — ein Dictionary ist hier richtig
und stirbt korrekterweise mit dem Prozess.

**KORREKTUR (gemessen, Task 5 Fix-Runde 2, 2026-09-12) — diese Annahme war
falsch:** "Die Bindung bleibt Loopback" stimmte nie mit dem gebauten Server
zusammen, der `0.0.0.0` bindet (`PLUGIN_SETUP_MCP_HOST`, Default `0.0.0.0`)
— das MUSS so sein, sonst erreicht der Container `/mcp` gar nicht. Und die
Folgerung "der Agent im Container erreicht den MCP-Teil über
`host.docker.internal`, der Mensch das Formular über `127.0.0.1` —
dieselbe Datei, **zwei Wege**" ist ebenfalls falsch: gemessen aus einem
laufenden Container (`marketing-claw`) heraus kommt eine Anfrage an
`host.docker.internal:<port>` beim Python-Prozess mit
`request.client.host == "127.0.0.1"` an — UND ZWAR AUCH GEGEN EINEN
LISTENER, DER NUR AUF `127.0.0.1` GEBUNDEN IST. Docker Desktops
`host.docker.internal`-Brücke terminiert Container-Traffic für den
Zielprozess wie eine lokale Loopback-Verbindung, unabhängig von dessen
Bind-Adresse. Es gibt also, jedenfalls auf einem Docker-Desktop/WSL-
Mirrored-Host wie diesem, **einen** Weg, nicht zwei — der Agent und der
Mensch sind an dieser Stelle vom Server aus nicht unterscheidbar. Das ist
die gemessene Wurzel der ganzen Fehlerklasse, die Task 5 Fix-Runde 1/2
schließen musste (rohe Messwerte: `task-5-report.md`,
`.superpowers/sdd/2026-09-12-eingabefenster/`). Was heute tatsächlich
verhindert, dass der Agent das Formular selbst absendet, ist NICHT diese
Bindung, sondern dass ihm keine Fähigkeit zur Verfügung steht, die eine
beliebige HTTP-Anfrage stellen könnte (Tool-Policy in
`config/openclaw.json`, s. `server.py`) — Konfiguration, nicht Struktur,
mit Test abgesichert (`tests/test_openclaw_tool_policy.py`), aber ohne
Beweis, dass (a) der laufende Container diese Datei so geladen hat oder
(b) keine hier nicht genannte Gruppe denselben Weg anderswo öffnet — sechs
Gruppen bleiben aus diesem Grund ungeprüft denied, s. `server.py` für
welche und warum das vorerst genügt; der echte Fix (die Adresse gar nicht
erst an den Agenten aushändigen) steht als Folgeaufgabe aus.

### E4 — Die schwebende Anfrage: 15 Minuten, einmal verwendbar

Sie hält Referenz, Projekt, Plugin, `art`, `ziel`, ein Token aus
`secrets.token_urlsafe` und eine Verfallszeit — **keinen Wert**, den gibt es
zu diesem Zeitpunkt nirgends. 15 Minuten sind lang genug, um im
Anbieter-Portal einen Schlüssel zu erzeugen, und kurz genug, dass ein
vergessener Link nicht wochenlang scharf bleibt. Erfolgreiches Absenden
verbraucht das Token.

### E5 — Drei Regeln für das Formular

- Der Wert reist per POST. Nie als Query-Parameter — der landet in
  Zugriffslogs.
- Die Seite spiegelt den Wert nie zurück, auch nicht maskiert.
- Bei Fehlschlag zeigt sie den **Statuscode** des Anbieters, nie den
  Antwortkörper.

### E6 — OAuth erbt den Provisioner

Für `art="oauth"` zeigt das Formular keinen Eingabekasten, sondern startet
den vorhandenen Fluss: Discovery, dynamische Registrierung, PKCE, Anmeldung
beim Anbieter im Browser des Menschen, Callback, Token-Tausch. Geändert wird
genau ein Ende — statt in eine Datei geht der Token in den internen
Schreibweg. Für den Menschen ist es derselbe Ablauf wie bisher.

### E7 — Ein Fehlschlag muss wiederholbar sein

Neue bewachte Funktion `plugin_setup.neu_aufnehmen(referenz)`, die
**ausschließlich** aus `fehlgeschlagen` heraus läuft, die Vault-Kopie löscht
und die Zeile für eine neue Aufnahme freigibt — nach dem Muster der drei
bestehenden Funktionen, als eigene Migrationsdatei `db/0005_neu_aufnehmen.sql`, weil `0001`
bis `0004` bereits auf der laufenden Instanz angewandt sind.

Ohne sie ist das Fenster im Alltag unbrauchbar: sich bei einem Schlüssel zu
vertippen ist der Normalfall. Sie schließt zugleich einen im Schlussreview
geparkten Befund.

---

## Was dieser Entwurf aufhebt

**D5 der Vorgänger-Spec** („Das Fenster ist ein sichtbarer Browser, den
openclaw treibt") wird ersetzt durch: **der Mensch benutzt seinen eigenen
Browser.**

Die Begründung ist eine Zielkollision, die erst beim Bauen sichtbar wurde:
*wenn der Agent das Fenster treibt, kann er die Seite auch lesen — also sieht
er den Wert ohnehin.* „Agent treibt das Fenster" und „Agent sieht den Wert
nie" schließen sich aus. D5 hat sich für das erste entschieden, bevor klar
war, dass es das zweite kostet.

Dazu kommen die gemessenen Befunde 2 bis 4: die Eindämmung, auf die D5 baute,
hält nicht; im Container ist ein sichtbares Fenster nicht herstellbar; und es
nativ zu teilen kostet eine CDP-Tür.

Der Zweck von D5 bleibt vollständig erhalten — *der Betreiber sieht, wo er
sich anmeldet, statt dem Agenten einen Wert zu diktieren.* Nur ist es jetzt
sein eigener Browser, und das ist per Konstruktion sichtbar.

---

## Was NICHT dazugehört

- **Kein Browser im Container**, kein VNC, keine CDP-Tür. Die
  Container-Variante wird durch diesen Entwurf gerade tragfähig, weil sie
  nichts davon mehr braucht.
- **Keine Authentifizierung am Formular** über das Einmal-Token hinaus.
  **KORREKTUR (gemessen, s. E3) — dieser Absatz widersprach der Korrektur
  dort, 70 Zeilen weiter oben, und wurde dabei uebersehen:** die Bindung ist
  NICHT Loopback, sie ist `0.0.0.0` (der Container muss `/mcp` erreichen,
  und dasselbe Binding trägt `/fenster/{token}`). "Wer beliebige Ports
  erreicht, hat ohnehin gewonnen" war genau die Schlussfolgerung, die die
  Messung widerlegt hat: der Container IST die Partei, vor der dieser
  Entwurf schützen soll, und er erreicht diese Ports über
  `host.docker.internal` wie jeder andere Aufrufer, ununterscheidbar vom
  Betreiber selbst (s. E3). Was das Formular heute tatsächlich davor
  bewahrt, dass der Agent es selbst bedient, ist NICHT die Bindung, sondern
  dass der Agent keine Fähigkeit hat, selbst eine HTTP-Anfrage zu stellen
  oder eine Shell zu benutzen (Tool-Policy in `config/openclaw.json`,
  `tools.deny`) — Konfiguration, nicht Struktur, und mit Test abgesichert
  (`tests/test_openclaw_tool_policy.py`), aber umkehrbar.
- **Keine Mehrbenutzer-Sitzungen.** Eine schwebende Anfrage gehört dem
  Menschen, der vor dem Rechner sitzt.
- **Kein Wiederherstellen schwebender Anfragen über einen Neustart.** Sie
  sterben mit dem Prozess; der Agent fordert dann neu an.
