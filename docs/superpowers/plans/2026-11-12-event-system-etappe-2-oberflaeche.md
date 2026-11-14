# Event-Management-System, Etappe 2: Oberfläche – Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Web-Oberfläche `event-web` für Orga-Teams:
- Katalog als Kacheln (Variante A)
- Event-Seite mit Reitern aus dem wirksamen Schema
- Anlegen in drei Schritten
- Login und Einladung
- Firmen-, Mitglieder- und Kontaktverwaltung

Dazu kommen die wenigen API-Ergänzungen, die die Oberfläche braucht.

**Architecture:**
- **Next.js-App:** liegt unter `spaces/event_management/web/`.
  - Sie spricht ausschließlich mit `event-api`, über einen Next-Rewrite `/api/* → EVENT_API_URL`. Cookie und CSRF bleiben dadurch same-origin.
  - Seiten sind Client-Komponenten mit einem kleinen typisierten API-Client. Es gibt keine eigene Serverlogik und keine zweite Fachlogik.
- **Generische Oberfläche:** Reiter und Formulare entstehen aus dem wirksamen Schema (`GET /api/events/{id}`) und aus dem Baustein-Verzeichnis (`GET /api/bausteine`, neu). Es gibt keine Seite pro Kategorie und keinen Code pro Baustein.
- **Aussehen:** wie im Learning Space (LearnHouse-Fork).

**Tech Stack:**
- Oberfläche: Next.js 16, React 19, TypeScript 6 (strict), Tailwind CSS 4 (`@tailwindcss/postcss`), lucide-react, clsx.
- Tests der Oberfläche: Vitest, @testing-library/react, jsdom.
- Paketmanager: npm (Node 24 ist lokal installiert).
- Backend-Ergänzungen: Python/FastAPI wie Etappe 1.
- Ende-zu-Ende-Test: pytest + Playwright (Python).

**Spec:** `docs/superpowers/specs/2026-11-12-event-management-system-design.md`. Gegenstand dieser Etappe sind die Abschnitte 3 (`event-web`) und 2 (Ziel 3). Etappe 1 ist auf master (vibemind-os `d8ca2b07`).

Nicht in dieser Etappe:
- Telegram-Gruppe verbinden und „Meinen Chat verbinden“ (die API dafür kommt mit Etappe 3; Schritt 3 beim Anlegen zeigt bis dahin einen Hinweis)
- Reiter Nachrichten
- Zusatz-Bausteine als Daten (die Oberfläche zeigt sie automatisch, sobald Etappe 3 sie ins Verzeichnis einträgt)
- Passwort vergessen
- Dockerfile und Ausrollen (Etappe 4)

## Global Constraints

- **Öffentliches Repo:** vibemind-os ist ÖFFENTLICH. Keine echten Firmen, Personen, Mails, IDs, URLs, Schlüssel. Beispiele: „Beispiel GmbH“, `@example.org`.
- **TypeScript:**
  - `strict: true`, **nie `any`** (stattdessen `unknown` und eingrenzen).
  - Kein `console.log` im committeten Code. Fehler werden angezeigt, nicht geloggt.
- **Styling:**
  - Nur Tailwind, keine Komponentenbibliothek außer lucide-react-Icons.
  - Aussehen wie im Learning Space:
    - Grundton grau/weiß.
    - Utility `nice-shadow` = `shadow-md shadow-gray-300/25 outline outline-1 outline-neutral-200/40`.
    - Kopfleiste fest, `h-[60px] bg-white/90 backdrop-blur-lg nice-shadow`.
    - Karten `bg-white rounded-xl nice-shadow overflow-hidden hover:scale-[1.01] transition-all duration-300`.
    - Kartentitel `text-base font-bold text-gray-900 line-clamp-1`.
    - Badges `px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide rounded-full`.
    - Inhalt in `max-w-screen-2xl mx-auto px-4 sm:px-6 lg:px-8`.
    - Schrift „Wix Madefor Text“ über `next/font/google` mit Fallback `system-ui`.
- **Sprache der Oberfläche:** Deutsch, mit echten Umlauten in Texten. Bezeichner in Code ASCII (deutsch wie im Backend, z. B. `ladeKatalog`).
- **Zeiten:**
  - Die API liefert ISO mit Offset. Angezeigt wird in Europe/Berlin (`Intl.DateTimeFormat('de-DE', {timeZone: 'Europe/Berlin'})`).
  - `datetime-local`-Eingaben werden als Berliner Wandzeit ohne Offset an die API geschickt. Die API deutet naive Zeiten als Berlin.
- **API-Zugriff:**
  - Nur über `web/lib/api.ts`.
  - Jede ändernde Anfrage sendet `X-CSRF` aus `/api/ich`.
  - 401 → Weiterleitung `/anmelden`.
  - 403 → Meldung „Keine Berechtigung“.
  - 404 → „Nicht gefunden“.
  - 422 → `detail` anzeigen.
  - 500 → „Interner Fehler“.
- **Python-Ergänzungen:** gleiche Regeln wie Etappe 1:
  - Fachschicht zuerst, die API ist nur eine Hülle.
  - Mandantenmatrix für neue Routen.
  - pytest mit `--basetemp` (Parent anlegen) und `-p no:playwright`.
- **Tests:**
  - Web: `npm test` (= `vitest run`) im Ordner `web/`.
  - Typprüfung: `npx tsc --noEmit` **im Ordner `web/`** (nie `npx --prefix`, das prüft nichts).
  - Build: `npm run build` muss durchlaufen.
- Conventional Commits, Commit-Texte enden mit `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- `web/node_modules/`, `web/.next/` und `web/next-env.d.ts` stehen in `web/.gitignore`. `package-lock.json` wird committet.

## Review Focus

1. **Event endet heute 23:30 Berlin:** Die Kachel steht um 23:00 unter „Läuft gerade“. Die Datumsanzeige zeigt die Berliner Uhrzeit, auch wenn der Browser in UTC läuft. Tests in Task 2 (`zeit.test.ts`, mit `TZ=UTC`) und Task 4.
2. **Abgelaufene Sitzung mitten in der Arbeit** (401 bei einem Speichern): Die Seite leitet nach `/anmelden?weiter=<pfad>` und nach dem Login zurück. Eingegebene Formulardaten gehen dabei verloren, das ist akzeptiert. Es darf nicht stumm nichts passieren. Test in Task 2 (`api.test.ts`).
3. **Mitglied ohne Leitungsrechte:**
   - Es sieht keinen Knopf „+ Neues Event“, „Löschen“, „Absagen“, „Einladen“, „Rolle ändern“.
   - Ruft es die Seite trotzdem direkt auf, zeigt die API-Antwort 403 eine klare Meldung.
   - Tests in Task 4, 6 und 8.
4. **Ein Baustein, den die Oberfläche (noch) nicht kennt** (Schema nennt `nachrichten` oder einen Zusatz ohne Eintrag in `/api/bausteine`): Der Reiter erscheint mit dem Hinweis „Dieser Bereich kommt mit dem Bot-Ausbau“, statt die Seite zu zerlegen. Test in Task 7.
5. **Lange Namen, leere Felder, Sonderzeichen:**
   - `<script>` im Event-Namen wird als Text angezeigt (React escaped; kein `dangerouslySetInnerHTML`).
   - Ein Event ohne Ort zeigt „Ort offen“.
   - Ein 200-Zeichen-Name bricht die Kachel nicht (`line-clamp-1`).
   - Tests in Task 4.

---

## Dateistruktur

```text
spaces/event_management/
  system/bausteine.py         # Typen-Vokabular: kontakt, ref:<eintrag>, url (Task 1)
  system/kern.py              # kontakt_aendern, kontakt_entfernen (Task 1)
  system/api.py               # GET /api/bausteine, Kontakt PATCH/DELETE (Task 1)
  tests/system/test_api_oberflaeche.py   # Task 1
  web/
    package.json  package-lock.json  tsconfig.json  next.config.ts  postcss.config.mjs
    vitest.config.ts  vitest.setup.ts  .gitignore
    app/
      layout.tsx  globals.css
      anmelden/page.tsx  einladung/[token]/page.tsx
      page.tsx                         # Katalog
      events/neu/page.tsx  events/[id]/page.tsx
      firmen/page.tsx  firmen/[id]/page.tsx
    components/
      Kopfleiste.tsx  AnmeldeSchutz.tsx  Meldung.tsx  Bestaetigen.tsx
      katalog/EventKachel.tsx  katalog/KatalogFilter.tsx
      formular/SchemaFeld.tsx  formular/BausteinFormular.tsx
      event/EventKopf.tsx  event/UebersichtReiter.tsx  event/BausteinReiter.tsx  event/VerlaufReiter.tsx
      firma/MitgliederListe.tsx  firma/EinladenFormular.tsx  firma/KontakteListe.tsx
    lib/
      api.ts  typen.ts  zeit.ts  phase.ts  rechte.ts  farbe.ts
    tests/   (*.test.ts / *.test.tsx, Vitest)
  tests/e2e/test_oberflaeche_e2e.py      # Task 9
```

---

### Task 1: API-Ergänzungen für die Oberfläche

**Files:**
- Modify: `system/bausteine.py`, `system/kern.py`, `system/api.py`
- Test: `tests/system/test_api_oberflaeche.py`; bestehende Tests anpassen, wo sich das Vokabular ändert.

**Interfaces:**
- Produces:
  - **Typen-Vokabular der REGISTRY** (ersetzt `uuid` bei Verweisen):
    - `kontakt`: Verweis auf `kontakt.id` derselben Firma.
    - `ref:<registry_eintrag>`: Verweis auf eine Zeile eines anderen Eintrags desselben Events. Beispiel: `aufgabe.kategorie_id` wird zu `ref:aufgaben_kategorien`.
    - `url`: http(s) wie R2.
    - Die Python-Prüfung behandelt `kontakt` und `ref:*` wie `uuid`. `uuid` bleibt nur, wo kein Verweis ist (heute keiner).
  - **`GET /api/bausteine`** (angemeldet, kein CSRF) liefert:

    ```json
    {"<eintrag>": {"baustein": "<schema-baustein>", "felder": {"<spalte>": "<typ>"}, "pflicht": ["<spalte>"], "reihenfolge": ["<spalte>", …]}}
    ```

    - `reihenfolge` ist die Anzeige-Reihenfolge der Spalten: die Einfügereihenfolge von `felder`.
  - **`kern.kontakt_aendern(conn, k, firma_id, kontakt_id, werte: dict) -> dict`:**
    - Erlaubte Schlüssel: `name`, `mail`, `telegram`.
    - Recht `baustein_schreiben`.
    - 0 Zeilen → `NichtGefunden`.
    - Die Textprüfung erfolgt über `system/pruefung.py`.
  - **`kern.kontakt_entfernen(conn, k, firma_id, kontakt_id) -> None`:**
    - Recht `mitglied_entfernen` (Leitung).
    - Wird der Kontakt noch verwendet (ForeignKeyViolation), folgt `ValueError("Kontakt wird noch verwendet")`.
  - **Neue Routen** (CSRF):
    - `PATCH /api/firmen/{id}/kontakte/{kid}` mit `KontaktAenderung` (`extra="forbid"`, Felder optional).
    - `DELETE /api/firmen/{id}/kontakte/{kid}` → 204.
  - **Antwort von `GET /api/ich`:** bekommt zusätzlich `rechte: {"event_anlegen": [firma_id…], "verwalten": [firma_id…]}`.
    - Das sind die Firmen, in denen die Person die jeweilige Aktion laut `rechte.py` darf.
    - Der Betreiber bekommt alle sichtbaren Firmen.
    - Die Oberfläche blendet damit Knöpfe ein und aus. Die API bleibt die Wache.

- [ ] **Step 1: Tests:**
  - `test_bausteine_route_liefert_registry_mit_typen`: `aufgaben.felder.kategorie_id == "ref:aufgaben_kategorien"`, `orga_team.felder.kontakt_id == "kontakt"`, `budget.pflicht` enthält `betrag_cent`.
  - `test_bausteine_route_ohne_cookie_401`.
  - `test_kontakt_aendern_und_entfernen`: Positivfall mit Leitung. Ein Mitglied darf ändern, aber nicht entfernen (403). Ein verwendeter Kontakt ergibt 422.
  - `test_kontakt_routen_mandantenmatrix`: Firma A gegen einen Kontakt von Firma B ergibt 404, das Admin-Nachlesen ist unverändert. Die neuen Routen kommen auch in die Liste von `test_matrix_deckt_alle_schreibenden_routen_ab`.
  - `test_ich_liefert_rechte`: Leitung in A und Mitglied in B ergibt `event_anlegen == [A]`, `verwalten == [A]`. Für den Betreiber stehen dort alle Firmen.
- [ ] **Step 2: rot.** **Step 3: implementieren.** **Step 4: grün** (`-W error`). Danach einmal der ganze Ordner `spaces/event_management/tests`.
- [ ] **Step 5: Commit** `feat(event-management): API-Ergaenzungen fuer die Oberflaeche (Bausteine, Kontakte, Rechte)`

---

### Task 2: Web-Grundgerüst, API-Client, Zeit und Phase

**Files:**
- Create: `web/package.json`, `tsconfig.json`, `next.config.ts`, `postcss.config.mjs`, `vitest.config.ts`, `vitest.setup.ts`, `.gitignore`, `app/layout.tsx`, `app/globals.css`, `lib/api.ts`, `lib/typen.ts`, `lib/zeit.ts`, `lib/phase.ts`, `lib/rechte.ts`, `lib/farbe.ts`, `components/Kopfleiste.tsx`, `components/AnmeldeSchutz.tsx`, `components/Meldung.tsx`, `components/Bestaetigen.tsx`
- Test: `web/tests/api.test.ts`, `zeit.test.ts`, `phase.test.ts`, `farbe.test.ts`, `kopfleiste.test.tsx`

**Interfaces:**
- **`package.json`:**
  - Scripts:
    - `dev`: `next dev -p 3170`
    - `build`: `next build`
    - `start`: `next start -p 3170`
    - `test`: `TZ=UTC vitest run` (über `cross-env`, damit es auf Windows läuft)
    - `typen`: `tsc --noEmit`
  - Feste Versionen der gleichen Hauptlinie wie im Learning Space: `next@16`, `react@19`, `react-dom@19`, `tailwindcss@4`, `@tailwindcss/postcss@4`, `typescript@6`, `vitest`, `@testing-library/react`, `@testing-library/user-event`, `jsdom`, `lucide-react`, `clsx`, `cross-env`.
- **`next.config.ts`:** `output: 'standalone'`, `rewrites: [{ source: '/api/:pfad*', destination: `${process.env.EVENT_API_URL ?? 'http://127.0.0.1:8170'}/api/:pfad*` }]`.
- **`lib/typen.ts`:** TS-Typen für alle Antworten, die die Oberfläche nutzt:
  - `Ich`, `Firma`, `Mitglied`, `Kategorie`, `WirksamesSchema` (`bausteine`, `felder`, `begriffe`, `eckdaten_pflicht`)
  - `Feld` (`id`, `typ`, `beschriftung`, `pflicht`, `vorgabe`, `optionen`)
  - `KatalogEvent` (Event-Felder, `kategorie_name`, `firma_name`, `phase`, `kennzahlen`)
  - `EventDetail`, `BausteinInfo`, `Zeile` (`Record<string, unknown>` mit `id: string`), `Kontakt`, `VerlaufEintrag`
  - Der Umsetzer leitet die Felder aus den echten Antworten von `system/api.py` ab und prüft sie gegen den Code.
- **`lib/api.ts`:**
  - `class ApiFehler extends Error { status: number; detail: string }`.
  - `api.holen<T>(pfad: string, params?: Record<string, string | undefined>): Promise<T>`.
  - `api.senden<T>(methode: 'POST'|'PATCH'|'DELETE', pfad: string, daten?: unknown): Promise<T>`:
    - holt den CSRF-Token einmal über `/api/ich` und hält ihn im Speicher;
    - bei 403 mit „CSRF“ im `detail` einmal neu holen und wiederholen.
  - `api.ich(): Promise<Ich | null>` (null bei 401).
  - Bei 401 aus einer anderen Anfrage als `ich` ruft `api.ts` `beiAbmeldung()` (Standard: `window.location.assign('/anmelden?weiter=' + encodeURIComponent(aktuellerPfad))`, in Tests ersetzbar).
  - `credentials: 'same-origin'`, JSON-Bodys. Bei 204 gibt es keinen Body.
- **`lib/zeit.ts`:**
  - `zeigeDatum(iso)` → „12.11.2026“.
  - `zeigeZeit(iso)` → „23:30“.
  - `zeigeSpanne(beginnIso, endeIso)` → „14.–15.05.2026“ oder „12.11.2026, 18:00–23:30“.
  - `alsEingabe(iso)` → `YYYY-MM-DDTHH:mm` in Berliner Wandzeit für `datetime-local`.
  - `ausEingabe(wert)` → derselbe String ohne Offset.
  - Alles mit `Intl` und `timeZone: 'Europe/Berlin'`, nie mit der Browser-Zeitzone.
- **`lib/phase.ts`:** `gruppiere(events: KatalogEvent[]) -> {laeuft, kommend, vergangen}`. Die Phase kommt von der API, hier wird nur gruppiert und sortiert wie in der API.
- **`lib/rechte.ts`:** `darfAnlegen(ich, firmaId?)`, `darfVerwalten(ich, firmaId)` aus `ich.rechte`.
- **`lib/farbe.ts`:** `kategorieVerlauf(kategorieId): string`. Ein fester Tailwind-Gradient je Kategorie aus einer Liste von 9 Paaren. Unbekannt → der erste aus einem stabilen Hash.
- **`Kopfleiste`:**
  - Logo-Text „VibeMind Events“, Links „Katalog“ (`/`) und „Firmen“ (`/firmen`, nur wenn `ich.betreiber` oder `verwalten` nicht leer).
  - Rechts der Personenname und „Abmelden“ (`POST /api/abmelden`, danach `/anmelden`).
- **`AnmeldeSchutz`:** lädt `api.ich()`. Bei null → `/anmelden?weiter=…`. Bis dahin ein Lade-Skelett (`bg-gray-200 animate-pulse`). Stellt `ich` per React-Context bereit (`useIch()`).
- **`Meldung`:** Fehler- und Erfolgsbanner (`ApiFehler` → Text nach Global Constraints).
- **`Bestaetigen`:** modaler Dialog mit Titel, Text, optional „zum Bestätigen den Namen eintippen“, Knöpfe „Abbrechen“ und „<Aktion>“.

- [ ] **Step 1: Tests (Vitest, `fetch` gemockt):**
  - `api.test.ts`:
    - CSRF wird einmal geholt und mitgeschickt.
    - CSRF-403 → einmal neu holen und erneut senden.
    - 401 → `beiAbmeldung` mit `weiter` (Review Focus 2).
    - 422 → `ApiFehler.detail` gesetzt.
    - 204 → `undefined`.
  - `zeit.test.ts` (TZ=UTC):
    - `2026-11-12T22:30:00+00:00` wird als „23:30“ angezeigt.
    - `alsEingabe`/`ausEingabe` sind Roundtrip-stabil.
    - Die Sommerzeit-Grenze 29.03.2026 stimmt.
  - `phase.test.ts`: Reihenfolge der Gruppen.
  - `farbe.test.ts`: Die Farbe ist stabil, eine unbekannte Kategorie liefert eine gültige Farbe.
  - `kopfleiste.test.tsx`:
    - Ein Mitglied ohne Verwaltungsrecht sieht „Firmen“ nicht.
    - Ein Betreiber sieht „Firmen“.
    - „Abmelden“ ruft die API.
- [ ] **Step 2: `npm install`, dann rot.** **Step 3: implementieren.** **Step 4: grün** (`npm test`, `npm run typen`, `npm run build`).
- [ ] **Step 5: Commit** `feat(event-management): event-web Grundgeruest, API-Client und Zeitlogik`

---

### Task 3: Anmelden und Einladung einlösen

**Files:**
- Create: `web/app/anmelden/page.tsx`, `web/app/einladung/[token]/page.tsx`
- Test: `web/tests/anmelden.test.tsx`, `web/tests/einladung.test.tsx`

**Interfaces:**
- **`/anmelden`:**
  - Karte mittig (`max-w-sm`), Felder Mail und Passwort, Knopf „Anmelden“.
  - Bei Erfolg: zu `weiter`, falls es ein relativer Pfad ist, der mit `/` beginnt und nicht mit `//`; sonst zu `/`. Das schützt vor offener Weiterleitung.
  - Fehler 401 → „Mail oder Passwort falsch, oder zu viele Versuche. Bitte später erneut versuchen.“
- **`/einladung/[token]`:**
  - Lädt `GET /api/einladung/{token}`. Bei `gueltig: false` → „Diese Einladung ist ungültig oder abgelaufen.“
  - Sonst „Einladung zu <firma_name>“.
  - Bei `braucht_passwort`: Name (vorbelegt leer) und Passwort zweimal. Prüfung clientseitig: mindestens 12 Zeichen und beide gleich; die Server-Antwort zählt.
  - Ohne Passwort-Bedarf nur „Einladung annehmen“.
  - Danach zu `/anmelden` mit dem Hinweis „Konto bereit, bitte anmelden“. Das Einlösen meldet nicht an (Entscheidung aus Etappe 1).

- [ ] **Step 1: Tests:**
  - Erfolgreiches Anmelden leitet auf `weiter=/events/x`.
  - `weiter=//boese.example` → `/`.
  - 401 → Meldung.
  - Einladung ungültig → Text.
  - Passwort zu kurz → Hinweis, kein Request.
  - Passwörter ungleich → Hinweis.
  - Erfolg → Weiterleitung mit Hinweis.
- [ ] **Step 2–4:** rot → implementieren → grün (`npm test`, `npm run typen`).
- [ ] **Step 5: Commit** `feat(event-management): Anmelden und Einladung einloesen`

---

### Task 4: Katalog (Kacheln, Variante A)

**Files:**
- Create: `web/app/page.tsx`, `web/components/katalog/EventKachel.tsx`, `web/components/katalog/KatalogFilter.tsx`
- Test: `web/tests/katalog.test.tsx`, `web/tests/kachel.test.tsx`

**Interfaces:**
- **Kopf der Seite:**
  - Titel „Katalog“.
  - Rechts „+ Neues Event“: nur bei `darfAnlegen(ich)`, Link auf `/events/neu`.
  - Darunter `KatalogFilter`:
    - Suchfeld „Suchen: Name, Ort, Firma …“, an `suche` der API gebunden, mit 300 ms Entprellung.
    - Firma-Auswahl „Alle Firmen“ plus die Firmen aus `ich.firmen` (bzw. `GET /api/firmen` beim Betreiber).
    - Kategorie-Chips „Alle <n>“ plus je Kategorie „<Name> <n>“. Die Zahlen kommen aus der aktuellen Trefferliste, gefiltert wird nach exakter Kategorie (Ruling Etappe 1).
- **Gruppen:**
  - „Läuft gerade“, „Kommend“ und „Vergangen“. „Vergangen“ ist eingeklappt (`▸`) und klappt per Klick auf.
  - Leere Gruppen werden nicht gezeigt.
  - Sind gar keine Events da, erscheint der Leer-Zustand (Search-Icon `text-gray-300`, „Keine Events gefunden“).
  - Raster `grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4`.
- **`EventKachel`:**
  - Oben ein Gradient-Streifen `h-[46px]` aus `kategorieVerlauf`.
  - Badges:
    - Kategorie (`bg-gray-100 text-gray-700`).
    - Phase: „läuft“ `bg-green-100 text-green-700`, „kommend“ `bg-blue-100 text-blue-700`, „vorbei“ `bg-gray-100 text-gray-500`.
    - Status „abgesagt“ `bg-red-100 text-red-700`, wenn abgesagt.
    - „Legacy“ `bg-yellow-100 text-yellow-700` bei `quelle == 'legacy'`.
  - Titel (`line-clamp-1`), Zeile „Firma · Spanne · Ort“ (ohne Ort: „Ort offen“; mit nur `ort_link`: „online“).
  - Fortschrittsbalken `aufgaben_gesamt - aufgaben_offen` / `aufgaben_gesamt` (bei 0 kein Balken), Fußzeile „Aufgaben x/y · z Zusagen“.
  - Klick:
    - auf `/events/<id>`;
    - bei Legacy auf `legacy_url` in einem neuen Tab (`rel="noopener noreferrer"`), aber nur, wenn die URL mit `https://` oder `http://` beginnt; sonst keine Verlinkung.

- [ ] **Step 1: Tests (API gemockt):**
  - Gruppen in der richtigen Reihenfolge, „Vergangen“ eingeklappt.
  - Chip-Zahlen stimmen, Chip-Klick filtert.
  - Die Suche schickt `suche` an die API, mit Entprellung (Fake-Timer).
  - Ein Mitglied ohne Anlegerecht sieht „+ Neues Event“ nicht (Review Focus 3).
  - Kachel mit `<script>`-Namen: Der Text erscheint wörtlich, es gibt kein Script-Element.
  - Ohne Ort → „Ort offen“.
  - Legacy mit `javascript:`-URL → kein Link.
  - Fortschritt 0/0 → kein Balken.
  - Ende 23:30 Berlin, Phase `laeuft` → unter „Läuft gerade“ (Review Focus 1).
- [ ] **Step 2–4:** rot → implementieren → grün (`npm test`, `npm run typen`, `npm run build`).
- [ ] **Step 5: Commit** `feat(event-management): Katalog mit Kacheln, Filtern und Gruppen`

---

### Task 5: Event anlegen in drei Schritten

**Files:**
- Create: `web/app/events/neu/page.tsx`, `web/components/formular/SchemaFeld.tsx`
- Test: `web/tests/anlegen.test.tsx`, `web/tests/schemafeld.test.tsx`

**Interfaces:**
- **Schritt 1 „Kategorie wählen“:**
  - Raster der wählbaren Kategorien (`GET /api/kategorien`).
  - Jede Karte zeigt den Namen und „+ <Zusätze>“: die Bausteine aus `GET /api/kategorien/{id}/schema`, die nicht im Kern sind, mit ihren `begriffe`-Namen. Für die Anzeige werden die Schemata aller Kategorien parallel geladen.
- **Schritt 2 „Eckdaten“:**
  - Firma: Auswahl aus `ich.rechte.event_anlegen`. Ist es nur eine, ist sie fest.
  - Name.
  - Beginn und Ende: `datetime-local`; Ende vorbelegt mit Beginn + 2 h, sobald Beginn gesetzt ist.
  - Ort als Adresse, Online-Link, Beschreibung. Pflicht ist, was `eckdaten_pflicht` verlangt, markiert mit `*`.
  - Danach die Metadaten-Felder des Schemas über `SchemaFeld`.
  - „Weiter“ erst, wenn die Pflichtfelder gefüllt sind und Ende ≥ Beginn.
- **Schritt 3 „Telegram-Gruppe verbinden (optional)“:**
  - Hinweis „Kommt mit dem Bot-Ausbau. Du kannst die Gruppe später auf der Event-Seite verbinden.“
  - Knopf „Event anlegen“ → `POST /api/events` → Weiterleitung `/events/<id>`.
  - 422 → `detail` unter dem Formular, zurück zum Schritt mit dem Fehler (Feldname im `detail`).
- **`SchemaFeld({feld, wert, aendern})`** bildet `Feld.typ` auf Eingaben ab:
  - `text` → `input`, `langtext` → `textarea`, `zahl` → `input type=number` (leer = nicht gesetzt).
  - `ja_nein` → Checkbox, `datum` → `type=date`, `datumzeit` → `datetime-local`.
  - `auswahl` → `select` über `optionen`, `url` → `type=url`.
  - Die Vorgabe ist vorbelegt. Beschriftung aus `beschriftung`, Pflicht mit `*`.

- [ ] **Step 1: Tests:**
  - Schritt 1 zeigt 9 Kategorien. Bei Hackathon steht „+ Teams, Abgaben, Jury“ (aus `begriffe`, falls vorhanden, sonst Baustein-Namen).
  - Webinar: „Weiter“ ist ohne Online-Link gesperrt.
  - Weihnachtsfeier: `wichteln` ist mit `false` vorbelegt.
  - Ende vor Beginn sperrt „Weiter“.
  - Abschicken sendet naive Berliner Zeiten und `metadaten` mit Typen (Zahl als `number`, Checkbox als `boolean`).
  - 422 zeigt `detail`.
  - Ein Mitglied ohne Anlegerecht, das `/events/neu` aufruft, bekommt einen Hinweis statt des Formulars.
  - `SchemaFeld` für jeden Typ.
- [ ] **Step 2–4:** rot → implementieren → grün.
- [ ] **Step 5: Commit** `feat(event-management): Event anlegen in drei Schritten aus dem Schema`

---

### Task 6: Event-Seite – Kopf, Reiterleiste, Übersicht, Verlauf

**Files:**
- Create: `web/app/events/[id]/page.tsx`, `web/components/event/EventKopf.tsx`, `UebersichtReiter.tsx`, `VerlaufReiter.tsx`
- Test: `web/tests/eventseite.test.tsx`

**Interfaces:**
- **Kopf:**
  - Brotkrumen „<Firma> › <Kategorie>“, Name (`h1 text-2xl font-bold`), Zeile „Spanne · Ort“.
  - Badges für Phase und Status.
  - Bei Legacy: ein Hinweis-Banner „Dieses Event wird in der alten App gepflegt“ mit Link (gleiche URL-Regel wie Task 4). Reiter gibt es dann nicht.
- **Reiterleiste:**
  - „Übersicht“, dann je Baustein des Schemas außer `verlauf` ein Reiter mit `begriffe[baustein] ?? Baustein-Name`, zuletzt „Verlauf“.
  - Der aktive Reiter steht im Query-Parameter `?reiter=`, damit Links und Neuladen stabil sind.
- **Übersicht:**
  - Eckdaten und Metadaten als Formular (`SchemaFeld`), Knopf „Speichern“ → `PATCH /api/events/{id}`.
  - Status-Bereich:
    - Jeder mit Recht kann „geplant“, „läuft“ und „abgeschlossen“ setzen.
    - „Absagen“ und „Archivieren“ nur bei `darfVerwalten` und mit `Bestaetigen`.
    - „Wieder aufnehmen“ (aus abgesagt oder archiviert) nur bei `darfVerwalten`.
  - „Event löschen“: nur `darfVerwalten`. `Bestaetigen` verlangt, den Event-Namen einzutippen. Danach geht es zum Katalog.
- **Verlauf:** Liste `GET /api/events/{id}/verlauf?limit=100` mit Zeit (Berlin), Actor, Baustein (Begriff), Aktion und Quelle.

- [ ] **Step 1: Tests:**
  - Die Reiter folgen dem Schema: `team_meeting` hat „Tagesordnung“ und keinen Budget-Reiter, `festival` hat „Timetable“.
  - Der Reiter steht in der URL.
  - Speichern schickt nur geänderte Felder.
  - Ein Mitglied sieht weder „Löschen“ noch „Absagen“ (Review Focus 3).
  - Löschen erst nach dem Eintippen des Namens.
  - 403 der API → „Keine Berechtigung“.
  - Legacy → Banner, keine Reiter.
  - Verlaufszeiten in Berlin.
- [ ] **Step 2–4:** rot → implementieren → grün.
- [ ] **Step 5: Commit** `feat(event-management): Event-Seite mit Reitern aus dem Schema, Uebersicht und Verlauf`

---

### Task 7: Baustein-Reiter generisch

**Files:**
- Create: `web/components/event/BausteinReiter.tsx`, `web/components/formular/BausteinFormular.tsx`
- Modify: `web/app/events/[id]/page.tsx`
- Test: `web/tests/bausteinreiter.test.tsx`

**Interfaces:**
- **`BausteinReiter({eventId, firmaId, baustein, begriffe, verzeichnis})`:**
  - `verzeichnis` = Antwort von `GET /api/bausteine`.
  - Zum Baustein gehören alle Einträge mit `info.baustein === baustein`. `aufgaben` umfasst so auch `aufgaben_kategorien`, als zweiter Abschnitt „Kategorien“.
  - Ohne Eintrag → Hinweis „Dieser Bereich kommt mit dem Bot-Ausbau“ (Review Focus 4).
- **Je Eintrag:**
  - Tabelle der Zeilen (`GET …/b/<eintrag>`) mit Spalten aus `reihenfolge`.
  - Knopf „+ Hinzufügen“ öffnet `BausteinFormular` als Zeile oder Karte. „Bearbeiten“ und „Löschen“ je Zeile; Löschen mit `Bestaetigen`.
  - Speichern → POST/PATCH, dann neu laden.
- **Darstellung je Typ:**
  - `kontakt` → Name des Kontakts aus `GET /api/firmen/{firmaId}/kontakte`.
  - `ref:<e>` → das Feld `titel` oder `name` der Zeile aus `<e>`.
  - `datumzeit` → Berlin.
  - Spalten, die auf `_cent` enden → Euro (`1.234,50 €`).
  - `auswahl:` → der Wert.
- **`BausteinFormular` bildet Typen auf Eingaben ab:**
  - `text` → `input`.
  - `ganzzahl` → `number`; `*_cent` → Euro-Eingabe, umgerechnet in ganze Cent ohne Fließkomma-Rundungsfehler: String parsen, Komma oder Punkt.
  - `datumzeit` → `datetime-local`.
  - `auswahl:a,b` → `select`.
  - `kontakt` → `select` der Kontakte plus Eintrag „+ Neuer Kontakt …“: ein kleines Inline-Formular Name/Mail → `POST /api/firmen/{id}/kontakte`, danach vorausgewählt.
  - `ref:<e>` → `select` der Zeilen von `<e>`.
  - `url` → `type=url`.
  - Pflichtfelder mit `*`; leere optionale Felder werden nicht gesendet.

- [ ] **Step 1: Tests (Verzeichnis-Fixture = echte Antwortform aus Task 1):**
  - Der Aufgaben-Reiter zeigt die Abschnitte „Aufgaben“ und „Kategorien“.
  - Anlegen mit `kategorie_id` aus der Ref-Auswahl.
  - Budget `12,50` → `betrag_cent: 1250`, `0,1` → `10`; `abc` → Hinweis, kein Request.
  - Neuer Kontakt inline → POST an die Kontakte, danach ausgewählt.
  - Löschen mit Bestätigung.
  - Baustein `nachrichten` → Hinweis (Review Focus 4).
  - 422 zeigt `detail`.
- [ ] **Step 2–4:** rot → implementieren → grün (+ `npm run build`).
- [ ] **Step 5: Commit** `feat(event-management): Baustein-Reiter generisch aus dem Verzeichnis`

---

### Task 8: Firmen, Mitglieder, Einladungen, Kontakte

**Files:**
- Create: `web/app/firmen/page.tsx`, `web/app/firmen/[id]/page.tsx`, `web/components/firma/MitgliederListe.tsx`, `EinladenFormular.tsx`, `KontakteListe.tsx`
- Test: `web/tests/firmen.test.tsx`

**Interfaces:**
- **`/firmen`:**
  - Liste der sichtbaren Firmen als Karten: Name, Kürzel, eigene Rolle.
  - Der Betreiber hat oben „+ Firma anlegen“ (Name, Kürzel `^[a-z0-9-]{2,32}$` clientseitig geprüft, Server zählt).
- **`/firmen/[id]`**, drei Abschnitte:
  - **Mitglieder:** Tabelle Name, Mail, Rolle.
    - Bei `darfVerwalten` je Zeile „Rolle ändern“ (Select leitung/mitglied → PATCH) und „Entfernen“ (`Bestaetigen` → DELETE).
    - 422 („letzte Leitung“) zeigt `detail`.
  - **Einladen** (nur `darfVerwalten`): Mail, Name, Rolle → `POST …/einladungen`, Erfolg „Einladung verschickt“.
  - **Kontakte:** Liste mit Suche.
    - Anlegen und Bearbeiten für alle mit Recht.
    - Entfernen nur `darfVerwalten`, mit Bestätigung. 422 „wird noch verwendet“ wird angezeigt.

- [ ] **Step 1: Tests:**
  - Der Betreiber sieht „+ Firma anlegen“, die Leitung nicht.
  - Kürzel `Beispiel GmbH` → Hinweis, kein Request.
  - Ein Mitglied sieht weder Einladen noch Rolle ändern noch Entfernen (Review Focus 3).
  - Die letzte Leitung wird nicht entfernt → `detail`.
  - Einladung sendet Mail, Name und Rolle.
  - Kontakt bearbeiten → PATCH nur mit den geänderten Feldern.
  - Kontakt entfernen, 422 → Meldung.
- [ ] **Step 2–4:** rot → implementieren → grün (+ `npm run build`).
- [ ] **Step 5: Commit** `feat(event-management): Firmen-, Mitglieder- und Kontaktverwaltung`

---

### Task 9: Ende-zu-Ende-Probe (echter Browser, echte API, Wegwerf-Postgres)

**Files:**
- Create: `spaces/event_management/tests/e2e/conftest.py`, `tests/e2e/test_oberflaeche_e2e.py`
- Modify: nichts an Produktivcode außer echten Fehlern, die die Probe findet. Diese werden in eigenen `fix:`-Commits behoben.

**Interfaces:**
- **Steuerung:** Die Probe läuft nur mit `EVENT_E2E=1` und vorhandenem `web/node_modules`, sonst `pytest.skip` mit Grund. Im Task wird sie mit `EVENT_E2E=1` ausgeführt und muss grün sein.
- **`conftest.py`:**
  - Nutzt die Fixtures aus `tests/system/conftest.py` (`pg_cluster` usw.). Diese als `pytest_plugins` einbinden oder gemeinsam nutzbar machen, ohne die Systemtests zu ändern.
  - Startet `event-api` in einem Thread (`uvicorn.Server` über `erstelle_app(...)` mit `SpeicherMailer`, `cookie_sicher=False`, freiem Port).
  - Startet `next build` einmal pro Sitzung (gecacht über einen Zeitstempel in `.next`), dann `next start` auf freiem Port mit `EVENT_API_URL`.
  - Prozesse ohne Pipe (stdout/stderr in Logdateien unter basetemp), Ende per Prozess-Handle, nie per Name.
- **Ablauf als ein Test `test_orga_ablauf_im_browser`** (Playwright Python, Chromium headless):
  1. Betreiber per Admin-SQL anlegen, mit `passwort_hash`.
  2. Anmelden, Firma „Beispiel GmbH“ anlegen.
  3. Leitung einladen; den Token aus `SpeicherMailer`, Einladung im Browser einlösen.
  4. Als Leitung anmelden.
  5. Hackathon anlegen (3 Schritte).
  6. Reiter „Aufgaben“: Kategorie und Aufgabe anlegen.
  7. Katalog zeigt die Kachel unter „Kommend“ mit „Aufgaben 0/1“.
  8. Der Verlauf zeigt die Einträge.
- **Zweiter Test `test_firma_b_sieht_nichts_im_browser`:**
  - Eine Leitung von Firma B ruft die Event-URL von A direkt auf und bekommt „Nicht gefunden“.
  - Der Katalog von B zeigt das Event nicht.

- [ ] **Step 1: Tests schreiben.** **Step 2: ausführen**, Fehler finden. **Step 3:** Ursachen beheben (eigene `fix:`-Commits, mit Unit-Test, wo möglich). **Step 4: grün** mit `EVENT_E2E=1`. Danach einmal den ganzen Python-Ordner und `npm test` laufen lassen.
- [ ] **Step 5: Commit** `test(event-management): Ende-zu-Ende-Probe der Oberflaeche`

---

## Nach der Etappe

Die Etappe ist fertig, wenn Python-Suite, `npm test`, `npm run typen`, `npm run build` und die E2E-Probe grün sind.
