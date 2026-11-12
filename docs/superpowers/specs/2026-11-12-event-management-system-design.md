# Event-Management-System: Katalog, Kategorien mit Vererbung, ein Bot für alle Events

Stand: 12.11.2026, Entwurf aus dem Gespräch mit Felix. Der Space `event-management` (`spaces/event_management/`) ist seit heute ausgerollt, mit einem Bot für genau ein Event, das Legacy-Festival. Dieses Dokument beschreibt den Umbau zu einem allgemeinen Event-Management-System.

## Absicht und Warum

Felix organisiert Events für sich und für andere Firmen: Webinare, Veranstaltungen, Hackathons, Messen, Team-Meetings, Weihnachtsfeiern, Feste. Bisher kann der Space genau ein Event, alles darin ist auf ein Festival zugeschnitten (Werkzeugnamen, Anweisungen, feste Werte für Name, Datum und Ort).

Ziel ist ein universelles Event-Management-System:
- **Katalog** aller Events, aufgebaut wie der Kurs-Katalog im Learning Space.
- **Kategorien** je Event-Art. Jede Kategorie erbt ein gemeinsames Grundschema und passt es an. Ein Event wird unter einer Kategorie angelegt und erbt deren Schema.
- **Der Bot verwaltet alles**, was die Oberfläche kann. Er bekommt genau die Werkzeuge, die zum Schema des Events passen.
- **Es ist immer klar, in welchem Event gearbeitet wird.** Das muss technisch erzwungen sein, nicht nur in den Anweisungen des Bots stehen.

## Ziele

1. **Alle Kategorien gleichzeitig**, aus einem gemeinsamen Grundschema (Felix: „mach alle gleichzeitig … ein Schema vererben und dann anpassen“):
   - Webinar, Veranstaltung, Hackathon, Messe, Team-Meeting, Weihnachtsfeier, Fest, Festival.
2. **Mandanten:**
   - Mehrere Firmen, streng getrennt.
   - Felix als Betreiber sieht alles.
   - Die Orga-Teams einer Firma sehen nur die Events ihrer Firma.
3. **Oberfläche nur für Orga-Teams**, im Stil des Learning Space:
   - Katalog als Kacheln (Variante A, von Felix gewählt), gruppiert nach „Läuft gerade“, „Kommend“ und „Vergangen“.
   - Filter nach Firma und Kategorie, Suche, „+ Neues Event“.
   - Event-Seite mit Eckdaten und Bereichen als Reiter (Kern plus die Zusätze der Kategorie).
   - Anlegen in drei Schritten: Kategorie, dann Eckdaten, dann optional eine Telegram-Gruppe verbinden.
4. **Ein Bot (ein OpenClaw-Gateway) für alle Events:**
   - Eine Telegram-Gruppe gehört zu genau einem Event.
   - Im Direktchat und über VibeMind wählt man das Event ausdrücklich.
   - Jede Bestätigung nennt das Event.
5. **Legacy:** Die alte Festival-App läuft weiter. Ihr Festival steht als Eintrag im Katalog.
6. **Brain-Anbindung:** Die bestehende Anbindung (`event.ask` / `event.do`) bleibt und wird auf den Katalog umgestellt.

## Nicht-Ziele

- **Kein Zugang für Teilnehmende und Gäste:** keine Anmeldeseiten, keine Zusage-Links. Teilnehmende pflegt das Orga-Team oder der Bot.
- **Keine Migration der Legacy-Daten** in das neue Modell.
- **Keine öffentliche Domain in dieser Etappe.** Zugang wie beim Learning Space nur über das Tailnet.
- **WhatsApp bleibt aus.** Das Plugin läuft nicht auf OpenClaw 2026.7.1.
- **Kein Bezahlen und keine Tickets.**

## Anti-Ziele (wäre ein Fehlschlag, auch wenn es technisch läuft)

- Der Bot schreibt im falschen Event oder in einer fremden Firma, gleich wie er sich vertut.
- Jemand aus Firma B sieht etwas von Firma A, in der Oberfläche oder über den Bot.
- Eine neue Kategorie braucht kopierten Code statt Vererbung.
- Private Daten (Firmen, Personen, Gruppen-IDs, Adressen, Schlüssel) landen im öffentlichen Repo `vibemind-os`.

## Gemessene Grundlagen (12.11.2026)

**Learning Space** (Vorbild für die Oberfläche):
- LearnHouse-Fork, Next.js mit Tailwind, FastAPI mit SQLModel/Alembic, eigene Postgres, Compose auf `offload-vm`, Zugang über das Tailnet.
- Mehrere Firmen kann LearnHouse nur in der EE-Lizenz. Der Learning Space behilft sich mit Gruppen in einer einzigen Organisation.
- **Folge:** Das Event-System wird eine **eigene App auf demselben Stack**, nicht eine Erweiterung des Forks. Übernommen werden Aufbau und Aussehen des Katalogs und der Kursseite (`apps/web/app/orgs/[orgslug]/(withmenu)/courses/`, `course/[courseuuid]/`).

**OpenClaw 2026.7.1** (gemessen per `config validate` und Doku im Image):
- **Gruppen binden:** Top-Level-`bindings` binden eine Telegram-Gruppe (`peer.kind: group`) oder einen Direktchat (`peer.kind: direct`) an eine Agenten-ID. `channels.telegram.groups.<id>.agent` gibt es nicht.
- **Neuladen:** `agents`, `bindings`, `mcp`, `tools` und `channels.*` werden ohne Neustart neu geladen (laut Doku; für `agents.list` gemessen).
- **MCP-Server sind global:** Pro Agent gibt es keine eigenen MCP-Server und keine eigenen Header. Ein Agent kann aber per `tools.allow` auf `<server>__*` beschränkt werden.
  - **Folge:** pro Event ein eigener Server-Eintrag `mcp.servers.<name>` mit eigenem Schlüssel im Header, dazu ein eigener Agent, der nur diese Werkzeuge darf.
- **Keine Identität beim MCP-Aufruf:** Ein MCP-Aufruf trägt weder Agent noch Sitzung noch Telegram-Absender. Rollen pro Person lassen sich im Werkzeug-Server **nicht** prüfen.

**Mandanten-Muster im Bestand:**
- sales-claw trennt pro Laden mit eigenem Schema, eigener Rolle und eigenem Deployment. Das ist schwer für viele Firmen und bietet keine Betreiber-Gesamtsicht.
- Marketing nutzt geteilte Tabellen mit einer `mandant`-Spalte und filtert in der Anwendung.
- Für Schlüssel mit Geltungsbereich gibt es Vorbilder:
  - `freigabe_schluessel` in sales-claw: nur der sha256-Hash wird gespeichert, verglichen wird mit `compare_digest`, der Schlüssel wird einmal angezeigt.
  - der Login per Einmal-Link aus `passwort_reset.py`.

**Shim und Gateway:** Der Space hat seit heute einen eigenen Shim (`event-management-shim`, Tagesbudget) und ein Gateway (`event-management-claw`) mit festen Subnetzen.

## Was entschieden ist

### 1. Datenmodell: Grundschema, Kategorien, Vererbung

Kategorien sind **Schema-Definitionen als Code** (YAML im Repo). Sie werden beim Start geladen und geprüft. Eine Kategorie hat genau eine Elternkategorie, die Wurzel ist `basis`. Das wirksame Schema eines Events entsteht, indem die Kette von `basis` bis zur Kategorie zusammengeführt wird:

- **`bausteine`:** Liste von Funktionsbausteinen.
  - Das Kind übernimmt die Bausteine der Eltern und kann eigene ergänzen oder geerbte abwählen (`ohne: [...]`).
- **`felder`:** Metadaten-Felder des Events (Name, Typ, Pflicht, Vorgabewert, Beschriftung).
  - Das Kind ergänzt Felder oder überschreibt Beschriftung, Vorgabe und Pflicht eines geerbten Feldes. Den Typ eines geerbten Feldes darf es nicht ändern; das wird beim Laden geprüft.
- **`begriffe`:** Anzeigenamen, die je Kategorie anders heißen, z. B. „Ablauf“ heißt beim Team-Meeting „Tagesordnung“ und beim Festival „Timetable“.
  - Sie wirken in der Oberfläche und in den Werkzeugbeschreibungen des Bots.

**Kern in `basis`** (jedes Event hat das):
- Eckdaten: Name, Kategorie, Firma, Beginn, Ende, Ort als Adresse oder Online-Link, Beschreibung, Status (Entwurf, geplant, läuft, abgeschlossen, abgesagt).
- Bausteine:
  - `aufgaben` (mit Kategorien und Status),
  - `orga_team` (Personen mit Rolle im Event),
  - `teilnehmende` (Personen mit Zusage-Status, ohne eigenen Zugang),
  - `ablauf` (Einträge mit Zeit, Titel, Verantwortlichen),
  - `budget` (Posten, Kosten, Einnahmen),
  - `nachrichten` (Entscheidung Felix, 12.11.):
    - Ans Orga-Team schreibt `event-api` per Telegram-Bot-API in die verbundene Gruppe des Events.
    - An Teilnehmende geht eine Mail über den sales-claw-Versandweg. Jede Sendung braucht vorher eine Freigabe per Klick (Muster Freigabe-Eingang). Der Bot entwirft nur, er verschickt keine Mail selbst.
    - Dafür speichert der Kontakt eine Mailadresse.
  - `verlauf` (Änderungsprotokoll, immer an).

**Zusatz-Bausteine** (nur wo die Kategorie sie wählt):
- `wettbewerb_teams`, `abgaben`, `jury`
- `aussteller`, `staende`
- `sprecher`, `online_raum`, `aufzeichnung`
- `protokoll`, `beschluesse`
- `verpflegung`, `programm`, `helferschichten`
- `buehnen`

**Startbaum** (die Vererbung ist gewollt, Details sind Sache des Umsetzenden):

```
basis
├── veranstaltung            (+ programm, verpflegung)
│   ├── fest                 (+ helferschichten)
│   │   ├── festival         (+ buehnen; Begriff Ablauf = Timetable)
│   │   └── weihnachtsfeier  (Felder: Motto, Wichteln ja/nein)
│   └── messe                (+ aussteller, staende)
├── online_event             (+ online_raum, sprecher, aufzeichnung; Ort = Online-Link Pflicht)
│   └── webinar
├── hackathon                (+ wettbewerb_teams, abgaben, jury)
└── team_meeting             (+ protokoll, beschluesse; Begriff Ablauf = Tagesordnung; ohne: budget)
```

Eine neue Kategorie ist nur eine neue YAML-Datei. Neuer Code ist nur nötig, wenn sie einen Baustein braucht, den es noch nicht gibt.

### 2. Speicherung und Mandanten

- **Ort:** Supabase-Postgres auf `offload-vm` (`debian-supabase-db-1`), die Quelle der Wahrheit. Eigenes Schema `event`, nummerierte Migrationen unter `spaces/event_management/db/` wie bei Marketing. Ausgeführt werden sie als `supabase_admin`. Die Laufzeitrolle `event_app` hat kein DDL.
- **Tabellen** (geteilt, Mandant über `firma_id`):
  - `person`: das Login, ohne `firma_id`. Eine Person kann mehreren Firmen angehören. Der Betreiber ist ein globales Flag an `person`.
  - `mitgliedschaft`: (person, firma, rolle `leitung` | `mitglied`).
  - `firma`, `event`.
  - `kontakt`: Personen in Events (Orga-Team, Teilnehmende, Sprecher, Jury usw.) als Kontakte **je Firma**, mit `firma_id`, Name, optional Mail und Telegram-Name, optional verknüpft mit einer `person`. Kontakte wandern nie zwischen Firmen.
  - Eine Tabelle je Baustein. Jede Baustein-Zeile trägt `event_id` und `firma_id`.
- **Trennung doppelt:**
  - **Anwendung:** Jeder Zugriff geht über eine Schicht, die `firma_id` und bei Bot-Schlüsseln die `event_id` aus dem Aufrufkontext setzt. Es gibt keine Abfrage ohne diese Filter.
  - **Datenbank:** Row Level Security als zweites Tor. Die Anwendung setzt pro Transaktion `set_config('event.firmen', …)` und, wo vorhanden, `event.event`. Die Policies erlauben nur passende Zeilen. Der Betreiber setzt ein eigenes Flag.
- **Mandantentests sind Pflicht:** Für jeden Baustein und jedes Werkzeug zeigt ein Test, dass Firma B und ein fremdes Event weder lesbar noch schreibbar sind, über die API wie über den Werkzeug-Server.
- **`verlauf`** speichert jede Änderung strukturiert (event_id, actor, quelle, zeitpunkt, baustein, aktion, ziel_id). Das ersetzt für neue Events das Freitext-Parsen der Legacy-Prüfung.

### 3. Anwendung: API, Oberfläche, Werkzeug-Server

Drei Dienste im bestehenden Compose-Projekt des Space, Code unter `spaces/event_management/`:

**`event-api`** (FastAPI, Python):
- Eine einzige Fachschicht für alles: Kategorien laden, Events, Bausteine, Mitgliedschaften, Einladungen, Schlüssel. Die Oberfläche und der Werkzeug-Server nutzen dieselbe Fachschicht, es gibt keine zweite Logik.

**`event-web`** (Next.js mit Tailwind):
- Aufbau und Aussehen wie der Learning Space. Katalog, Event-Seite und Anlegen wie im Entwurf mit Felix (Katalog Variante A).
- Reiter und Formularfelder werden aus dem wirksamen Schema erzeugt, es gibt keine Seite pro Kategorie.
- Login per Einladung per Mail: Einmal-Link, Passwort setzen, Sitzungs-Cookie mit HMAC, CSRF. Vorbild sind `passwort_reset.py` und `ui.py` aus sales-claw.
- Mail über den bestehenden Betreiber-Versandweg.
- **Rollen:**
  - Betreiber: Firmen anlegen, alles sehen.
  - Leitung: Events der Firma anlegen und löschen, Leute einladen, Gruppen verbinden.
  - Mitglied: in den Events der Firma arbeiten.

**`event-mcp`** (MCP Streamable HTTP):
- Die Werkzeuge werden aus den Bausteinen erzeugt. Jeder Baustein liefert seine Werkzeuge mit, z. B. `aufgaben_liste`, `aufgabe_anlegen` und `aufgabe_aendern`. Beschreibungen nutzen die `begriffe` der Kategorie.
- Der Aufrufkontext kommt **nur** aus dem Schlüssel im Header, nie aus Argumenten des Modells.
- Schlüsselarten (Hash in der DB, einmal angezeigt, widerrufbar, Ablaufdatum):

| Art | Gebunden an | Rechte | `tools/list` |
|---|---|---|---|
| `event` | ein Event | Mitglied | nur die Werkzeuge der Bausteine dieses Events, ohne Marke |
| `leitung` | eine Person (alle Firmen, in denen sie Leitung ist) | Leitung | Baustein-Werkzeuge plus `events_suchen`, `event_waehlen`, `event_anlegen` und Verwaltung (einladen, Rolle ändern, Gruppe verbinden, Event absagen/archivieren/löschen, Kontakt entfernen) |
| `betreiber` | alles | Betreiber | wie `leitung`, über alle Firmen, plus `firma_anlegen` |
| `betreiber_lesen` | alles | nur lesen | nur lesende Werkzeuge plus `events_suchen`, `event_waehlen` |
| `melden` | nichts | nur `gruppe_melden` / `chat_melden` | genau dieses eine Werkzeug |

- **Event-Wahl über eine Marke statt über Serverzustand.** Bei `leitung`, `betreiber` und `betreiber_lesen` liefert `event_waehlen(suche)` eine **Marke** zurück. Das ist ein zufälliges, vom Server ausgestelltes Kürzel, gebunden an (Schlüssel, Event), gültig 30 Minuten. Jedes Baustein-Werkzeug verlangt die Marke als Argument.
  - Das Modell kann damit nur in einem Event arbeiten, das es in diesem Gespräch ausdrücklich gewählt hat. Mit einer Event-ID kann es nicht raten, und eine Marke eines anderen Schlüssels gilt nicht.
  - Es gibt keinen geteilten „zuletzt gewählt“-Zustand, den zwei Gespräche gegenseitig verstellen könnten.
  - Ohne gültige Marke schreibt kein Werkzeug.
  - Ein Werkzeug, dessen Baustein das Event nicht hat, antwortet mit einem klaren Fehler.
- **Riskante Aktionen in zwei Schritten.** Für Absagen, Archivieren, Löschen, Kontakt entfernen und Rolle ändern gilt:
  1. Der erste Aufruf ändert nichts. Er liefert eine Zusammenfassung mit dem Event-Namen und einen Bestätigungscode, gültig 5 Minuten.
  2. Erst ein zweiter Aufruf mit diesem Code führt die Aktion aus.

  Der Bot fragt dazwischen nach. Firmen anlegen geht nur mit `betreiber`.
- Jede Antwort eines schreibenden Werkzeugs nennt das Event (Name, Firma).
- **Legacy-Events** (`quelle: legacy`) lehnt `event-mcp` für alle Baustein-Werkzeuge ab, mit dem Hinweis auf die alte App (siehe Abschnitt 6).

### 4. Bot: ein Gateway, ein Agent pro Event

Im bestehenden `event-management-claw`:

**Gruppen-Agent pro Event:**
- Agent `ev-<kurz>` mit eigenem Arbeitsbereich und SOUL, erzeugt aus dem wirksamen Schema (Name, Datum, Ort, Begriffe, Bausteine).
- Er greift nur auf `tools.allow: ["ev-<kurz>__*"]` zu.
- Dazu kommt ein Eintrag `mcp.servers.ev-<kurz>` (URL `event-mcp`, Header mit `event`-Schlüssel) und ein Binding der Telegram-Gruppe an den Agenten.
- Das Event ist damit technisch fest.

**Leitungs-Agent pro Leitungsperson:**
- Agent `ld-<kurz>`, gebunden an den Telegram-Direktchat dieser Person (`peer.kind: direct`, `dmScope: per-peer`), mit `leitung`-Schlüssel.
- Ist die Person Leitung in mehreren Firmen, bleibt es ein Agent und ein Schlüssel. Die Firma ergibt sich aus dem gewählten Event.
- Weil MCP-Aufrufe den Absender nicht kennen, hängen die Leitungsrechte am Direktchat der Person, nicht an der Gruppe. In der Gruppe hat der Bot immer nur Mitgliedsrechte.
- **Direktchat verbinden,** wie bei der Gruppe:
  - In der Oberfläche erzeugt die Person unter „Meinen Telegram-Chat verbinden“ einen Einmal-Code und schickt ihn dem Bot im Direktchat.
  - Der Empfangs-Agent meldet Code und Chat-ID per `chat_melden(code)`.
  - Die Person bestätigt in der Oberfläche, erst dann entsteht der Agent.
  - Einen Direktchat ohne bestätigten Code bekommt nie einen Leitungs-Agenten.
- Mitglieder haben keinen eigenen Direktchat-Agenten. Sie arbeiten in der Gruppe ihres Events.

**Betreiber:** Felix bekommt auf demselben Weg einen Direktchat-Agenten mit `betreiber`-Schlüssel.

**Empfangs-Agent `main` für Unbekannte:**
- Er hat nur den `melden`-Schlüssel, also keine schreibenden Werkzeuge.
- In nicht verbundenen Gruppen erklärt er, wie man verbindet. Im Direktchat von Unbekannten verweist er an die Leitung.
- **Kosten durch Fremde begrenzen:**
  - In nicht verbundenen Gruppen reagiert der Bot nur, wenn er erwähnt wird (`requireMention`).
  - Der Empfangs-Agent antwortet kurz.
  - Das Tagesbudget des Shims steigt von 100 auf 300.
  - Bei 80 % geht eine Meldung an den Betreiber.
  - Grenzen je Firma sind in dieser Ausbaustufe kein Ziel. Ob `groupPolicy` sich für fremde Gruppen enger fassen lässt, wird in Etappe 3 gemessen.

**Gruppe verbinden:**
- In der Oberfläche erzeugt die Leitung beim Event einen Einmal-Code und fügt den Bot der Gruppe hinzu. In der Gruppe schickt man den Code.
- Der Empfangs-Agent meldet Code und Chat-ID an `event-api`. Dafür hat er genau ein Werkzeug `gruppe_melden(code)`, mit eigenem Schlüssel nur dafür.
- Die API prüft per Telegram `getChat`, dass der Bot in dieser Gruppe ist, und zeigt der Leitung in der Oberfläche den Gruppentitel zur Bestätigung.
- Erst nach dieser Bestätigung entstehen Agent, Server-Eintrag und Binding.
- **Messpunkt:** Ob der Empfangs-Agent die Chat-ID zuverlässig im Kontext hat, wird in Etappe 3 zuerst gemessen. Wenn nicht, hält Etappe 3 an dieser Stelle an und legt Felix andere Wege vor; die übrigen Teile der Etappe hängen nicht daran.

**Bereitstellung:**
- Ein Arbeiter (Vorbild sales `admin_auftraege` mit Timer) setzt Agent, Server-Eintrag und Binding per `openclaw config set` im Container.
- Das Gateway lädt ohne Neustart neu.
- Beim Archivieren eines Events werden sie wieder entfernt und der Schlüssel widerrufen.

### 5. Brain und VibeMind

- `event.ask` und `event.do` bleiben. Jeder Brain-Agent hat einen eigenen Schlüssel:
  - `brain-lesen` bekommt `betreiber_lesen`.
  - `brain-auftrag` bekommt `betreiber`.
- Der Brain-Text muss das Event nennen. Der Agent wählt es per `event_waehlen` und arbeitet mit der Marke. Bei mehreren Treffern antwortet er mit der Trefferliste und schreibt nicht.
- **Legacy-Festival:** Für dieses eine Event behalten beide Brain-Agenten zusätzlich ihre bisherigen `event-app__*`-Werkzeuge.
  - Ihre SOUL sagt: Ist das gewählte Event ein Legacy-Event, nimm die alten Werkzeuge.
  - `event-mcp` lehnt Legacy-Events ohnehin ab. Ein Schreiben ins falsche System ist dadurch ausgeschlossen.
- Die Wahrheitsprüfung `event_app_change` bekommt eine zweite Quelle: den strukturierten `verlauf` der neuen API, gefiltert nach event_id und actor „VibeMind“.
  - Das schließt die bekannte Fälschungslücke des Freitexts für neue Events.
  - Für das Legacy-Event bleibt der bisherige Weg.

### 6. Legacy-Festival

- Es steht im Katalog als Event der Kategorie `festival` mit dem Kennzeichen `quelle: legacy`.
- Seine Daten bleiben in der alten App. Die Kachel zeigt Eckdaten aus dem Katalog und verlinkt die alte App.
- Der heutige Gruppen-Agent `main` mit den Werkzeugen der alten App bleibt für diese eine Gruppe. Er wird umbenannt zu `ev-legacy` und per Binding an die Gruppe gebunden, damit `main` frei wird für den Empfang.

### 7. Betrieb

- Alles bleibt im Compose-Projekt des Space auf `offload-vm`, mit festen Subnetzen (die Docker-Adresspools dort sind erschöpft).
- Images werden gedeckelt gebaut (Erfahrung Learning-Web-Build).
- Zugang zur Oberfläche nur über das Tailnet.
- Der Gateway-Port für den Brain bleibt Loopback, bis die Freigabe (z. B. `tailscale serve`) entschieden ist.
- Vor jedem Ausrollen gilt der Claim im WORKBOARD bzw. in der maschinenweiten Koordination.

## Etappen (je ein eigener Plan)

1. **Fundament:**
   - Kategorie-Loader mit Vererbung und Prüfung, alle Kategorien als YAML.
   - Schema `event` mit Migrationen und RLS.
   - Fachschicht für Kern-Bausteine, `event-api` mit Login, Einladung und Rollen.
   - Mandantentests.
2. **Oberfläche:** Katalog (Kacheln), Event-Seite mit Reitern aus dem Schema, Anlegen, Einladen, Firmenverwaltung für den Betreiber.
3. **Werkzeug-Server und Bot:**
   - `event-mcp` mit Schlüsselarten und Event-Wahl.
   - Zusatz-Bausteine.
   - Gruppen-, Leitungs- und Empfangs-Agenten, Gruppe verbinden, Bereitstellungs-Arbeiter.
4. **Brain, Legacy, Ausrollen:** Brain auf den Betreiber-Schlüssel, `verlauf`-Prüfung, Legacy-Eintrag und Umbenennung, Ausrollen.

Zuerst muss Etappe 1 stehen. Die Etappen 2 und 3 bauen darauf auf und können danach parallel laufen.

## Entscheidungen des Autors nach der Bauprüfung (Felix kann sie umwerfen)

- **Event-Wahl per Marke:** Die Wahl läuft über eine Marke mit 30 Minuten Gültigkeit statt über einen gespeicherten „aktuellen“ Zustand pro Schlüssel. So können sich parallele Gespräche (z. B. zwei Brain-Aufrufe) das Event nicht gegenseitig verstellen.
- **Eigene Schlüssel für die Brain-Agenten:** `brain-lesen` bekommt einen Lese-Schlüssel, `brain-auftrag` einen eigenen.
- **Direktchats wie Gruppen verbinden:** Direktchats werden wie Gruppen per Einmal-Code und Bestätigung in der Oberfläche verbunden. Mitglieder haben keinen eigenen Direktchat-Agenten.
- **Personen und Kontakte getrennt:** `person` (Login, mehrere Firmen möglich) ist getrennt von `kontakt` (Personen in Events, je Firma, wandern nie zwischen Firmen).
- **Legacy-Festival:** Es bleibt für den Brain über die alten Werkzeuge erreichbar. `event-mcp` lehnt Legacy-Events ab.
- **Schutz vor Kosten durch Fremde:** Erwähnungspflicht in fremden Gruppen, Tagesbudget 300, Warnung bei 80 %. Keine Grenzen je Firma.
- **Riskante Aktionen zweistufig:** Für riskante Aktionen des Bots gibt es einen Bestätigungscode mit 5 Minuten Gültigkeit (Entscheidung Felix: „Ja, mit Rückfrage“).

## Was dem Umsetzenden überlassen ist

- Genaue Felder je Kategorie und genaue Werkzeugliste je Baustein, im Rahmen von Abschnitt 1 und 3.
- ORM und Migrationswerkzeug: SQLModel und Alembic wie beim Learning Space, oder nummerierte SQL-Dateien wie bei Marketing. Eins davon, durchgängig.
- Komponenten-Bibliothek der Oberfläche, solange das Aussehen dem Learning Space folgt.
- Form der SOUL-Vorlage für Gruppen-Agenten.

## Offene Punkte (werden in der jeweiligen Etappe zuerst gemessen)

- **Chat-ID:** Bekommt ein Agent in OpenClaw 2026.7.1 die Telegram-Chat-ID der Gruppe zuverlässig in den Kontext (Etappe 3)?
- **Werkzeuglisten:** Filtert `tools.allow` mit `<server>__*` zur Laufzeit wirklich? Bisher ist nur das Schema validiert (Etappe 3).
- **Neuladen:** Laden `bindings` und `mcp.servers` im laufenden Gateway ohne Neustart (Etappe 3)?
- **Last:** Speicher und Platte pro zusätzlichem Agenten auf `offload-vm` (Etappe 3, vor dem Ausrollen messen).
