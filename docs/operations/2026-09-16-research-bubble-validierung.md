# Sheerlay-Validierungslauf — echter Lauf durchgeführt, unabhängig verifiziert

**Datum:** 2026-09-16
**Autor:** Claude Code (Task 7, `2026-09-16-research-bubble-kopplung`-Plan)
**Ergebnis:** DONE_WITH_CONCERNS. Ein echter, bezahlter Recherchelauf gegen die
lokale Infrastruktur wurde durchgeführt, unabhängig gegen Datenbank und Reportdatei
verifiziert (nicht gegen den Rückgabewert des Tools), und ergab einen inhaltlich
starken, ehrlich lückenhaft gekennzeichneten Report. Ein reales Engineering-Problem
im Tool wurde dabei entdeckt und transparent umschifft (siehe Abschnitt „Entdeckte
Abweichung" unten).

Vorlauf: Diese Task war zunächst blockiert, weil `SUPABASE_SERVICE_ROLE_KEY`
entgegen der Auftragsbehauptung nicht in `.env` vorhanden war (siehe Git-Historie
dieser Datei für den ursprünglichen BLOCKED-Stand). Der Koordinator hat den Key
anschließend in `.env` ergänzt (nicht versioniert, nie ausgegeben) und beide
Endpunkte nach einem zwischenzeitlichen Docker-Neustart erneut verifiziert; ich habe
das vor Beginn selbst nochmal frisch geprüft (`/api/health`=200,
`researcher-hand`=Running, Supabase-REST=200).

## Bubble

```
POST /rest/v1/ideas → 201
id: 4bdaa482-755c-4284-96b0-750c3ce5b1d6
title: "Sheerlay"
```

## Vorschau und Handbearbeitung

`research_start` ohne `confirm` über `spaces/research/mcp_server.py` als echten
stdio-JSON-RPC-Subprocess-Client (initialize + tools/call) angesprochen. Geprüft:
Bubble-Kontext-Marker vorhanden (Bubble hat keine `canvas_nodes`, daher „keine
Inhalte hinterlegt" — korrekt), absoluter Ausgabepfad vorhanden, `Tiefe: thorough`,
`Zitierweise: academic_apa`, `Sprache: german` gesetzt. Der Abschnitt „Erwartetes
Ergebnis" war wie angekündigt dünn (mechanische Extraktion griff auf die
„Aufgabe"-Überschrift statt auf „Ergebnis" zu). Von Hand ersetzt durch eine
sechsteilige Liste konkreter Deliverables (Wettbewerbsprofile, Nutzerperspektive,
Strategy Canvas mit 5-8 Faktoren, ERRC-Raster, 3-5 USP-Positionierungen, durchgängige
APA-Belege mit Abrufdatum).

## Entdeckte Abweichung: `final_brief` und Job-ID sind entkoppelt

`mcp_server.py` erzeugt bei **jedem** Aufruf von `research_start` — Vorschau wie
Bestätigung — eine neue, unabhängige `job_id` und daraus einen neuen
`report_path`. Ein `final_brief`, das (wie vom Arbeitsablauf verlangt) aus dem Text
der Vorschau gebaut wird, trägt deshalb zwangsläufig den absoluten Pfad der
**Vorschau**-Job-ID in seinem „[Pflicht]"-Abschnitt — nicht den der eigentlichen,
erst beim Bestätigen erzeugten Job-ID. Der Agent schreibt folgerichtig an den alten
(Vorschau-)Pfad; `research_status` fragt aber den neuen, korrekten Pfad ab und würde
ohne Eingriff für immer `pending` bleiben. Das ist vor dem Bestätigungsaufruf am Code
nachvollzogen worden (`mcp_server.py` Zeilen 410/432/438/447/454/465), nicht erst
nach einem Fehlschlag entdeckt.

Reaktion: Lauf trotzdem gestartet (der Fund betrifft nur die Ablage-Bequemlichkeit,
nicht die Echtheit des Laufs), Dateisystem direkt beobachtet statt `research_status`
im Leerlauf abzufragen, und nach Auftauchen der echten Reportdatei am
Vorschau-Pfad die Datei **hash-verifiziert kopiert** (nicht verschoben — das
Original bleibt erhalten) an den von `research_status` erwarteten Pfad, bevor
`research_status` einmalig aufgerufen wurde, um die eingebaute, echte
Persistenzlogik (Zitatzählung aus der echten Datei, DB-Schreibvorgänge) tatsächlich
laufen zu lassen — nichts davon wurde von Hand nachgebaut.

```
sha256(Vorschau-Pfad) = 05a17fa03bd07e80e48a9915fee49d4e43aff2042d0c27f3411e54d414cc415f
sha256(korrekter Pfad)= 05a17fa03bd07e80e48a9915fee49d4e43aff2042d0c27f3411e54d414cc415f
identisch = True
```

**Empfehlung:** Vor breiterer Nutzung dieses Tools beheben — z. B. `research_start`
eine vom Aufrufer vorgegebene `job_id` akzeptieren lassen, oder den im
`final_brief` eingebetteten Pfad serverseitig durch den tatsächlich aktuellen
ersetzen. Jeder Operator, der dem im Task-Ablauf beschriebenen Vorgehen (Vorschau →
Handbearbeitung → `final_brief`) folgt, trifft sonst zuverlässig auf dieselbe Falle.

## Der Lauf

```
job_id:      job_v1_01M2N5VPP9T2PX2GJ7NG3F9XHN
gestartet:   15:17:52
Report da:   zwischen 15:29:52 und 15:30:52 (57.454 Bytes am Vorschau-Pfad)
Laufzeit:    ca. 12-13 Minuten
Kosten:      nicht ermittelbar — `GET /api/usage` zeigt für `researcher-hand`
             `tool_calls: 0, total_tokens: 0` (Tracking offenbar nicht verdrahtet
             oder durch den Docker-Neustart zurückgesetzt). Keine Zahl erfunden.
```

## Unabhängige Verifikation (nicht dem Rückgabewert geglaubt)

```
GET /rest/v1/research_report_artifacts?order=created_at.desc&limit=1
→ artifact_ref=artifact_v1_01M2N6MBXXET694BXAD410GPE0
  job_id=job_v1_01M2N5VPP9T2PX2GJ7NG3F9XHN
  bubble_id=4bdaa482-755c-4284-96b0-750c3ce5b1d6   (== Sheerlay-Bubble)
  citation_count=41  depth=thorough  output_style=detailed
  internal_context_used=true  context_disclosure=null

GET /rest/v1/canvas_nodes?linked_idea_id=eq.4bdaa482-755c-4284-96b0-750c3ce5b1d6
→ id=aae8ea79-a234-4e57-9810-eacec3712e34, title="Research: Sheerlay"
  linked_idea_id == dieselbe Bubble, metadata.artifact_ref stimmt überein
```

Zitatzahl selbst nachgerechnet (unabhängig von Tool-Rückgabewert und DB-Zeile):
`grep` aller eindeutigen `https?://`-URLs im Reportfile → **41** — exakter Treffer.

Reportfile direkt gelesen (nicht die DB-Kopie): 437 Zeilen, durchgängig Deutsch,
APA-Quellenverzeichnis (~35 Einträge) mit „Abgerufen am 16. September 2026 von
<URL>" je Eintrag, eigener Abschnitt für Informationslücken statt Schätzung, eigener
Abschnitt der explizit Auftragsannahmen widerspricht.

## Ehrliche Qualitätsbewertung

**Gut geliefert:** Alle drei direkten Wettbewerber adressiert — mit einem echten
Befund: „Taxome" ist nirgends auffindbar (Suchmaschinen, Domains, Firmenregister,
App-Stores geprüft); der Report benennt das explizit als ungeklärt statt ein Profil
zu erfinden, und ersetzt ersatzweise mit dem einzigen inhaltlich passenden echten
Fund („Toxome"). Alle fünf benachbarten Apps vollständig profiliert. Vier weitere,
selbst recherchierte Anbieter sauber als Ergänzung gekennzeichnet. Strategy Canvas
mit 7 Faktoren (im geforderten 5-8-Rahmen), jede Zelle mit Kurzbegründung. Volles
ERRC-Raster plus dreistufige Nicht-Kunden-Analyse mit echter zitierter Quelle. 5
USP-Positionierungen (oberes Ende von 3-5), jede auf allen drei geforderten Achsen
bewertet, mit benannter Schwachstelle und begründeter Gesamtempfehlung. Eigener
Abschnitt widerspricht expliziten Auftragsannahmen — genau wie gefordert. Trennt
durchgängig Selbstdarstellung von unabhängig Überprüfbarem (z. B. FiberChecks
Marketing-Claim „4,8★/10.000+" vs. tatsächlich 5,0★ bei n=4 im App Store).

**Nicht geliefert / echte Schwächen:**
- **Foren-/Social-Media-Auswertung ist dünn.** Praktisch nur App-Store-Bewertungen
  plus einige Verbraucher-Blogs und ein offizieller Verbraucherzentrale-Test;
  keine erkennbare Auswertung von Reddit, Facebook-Gruppen, Instagram/TikTok. Diese
  explizit geforderte Dimension ist unterdeliverert.
- **Nutzerperspektive der drei direkten Wettbewerber bleibt praktisch leer** — ehrlich
  begründet (4-12 Bewertungen je App, statistisch nicht belastbar), aber genau für
  die relevantesten drei Anbieter liefert der Report keine belastbare Aussage dazu,
  was Nutzer:innen konkret loben oder frustriert.
- **Regulatorik-Quellen (DPP/ESPR) vergleichsweise schwach**: gestützt auf
  „Gorilla.green" und „DPP.cloud" — beides eher Marketing-/Blog-nahe Seiten, keine
  Primärquellen (EU-Amtsblatt, Europäische Kommission), obwohl Positionierung 4
  genau darauf aufbaut.
- **Datenbankumfang bei allen drei direkten Wettbewerbern als fehlend markiert** —
  ehrlich, aber eine angeforderte Dimension bleibt für die wichtigste Gruppe offen.
- **Tiefe bewusst auf `thorough` begrenzt** (Kostenvorgabe) statt `exhaustive` — 41
  Quellen auf ca. 13 profilierte Anbieter verteilt, angemessen für einen ersten
  Durchgang, sichtbar flacher als ein `exhaustive`-Lauf gewesen wäre.

**Gesamtbild:** ein inhaltlich starker, strukturell vollständiger, selbstkritisch
ehrlicher Report — deutlich über „dünnes KI-Blabla" hinaus. Die größte echte Lücke
ist die Nutzer-/Forenperspektive gerade bei den direkten Wettbewerbern (teils
Marktrealität, teils echte Recherchelücke), dazu die schwächere Regulatorik-Quellenlage
bei Positionierung 4 — beides sollte ein Mensch gegenlesen, bevor daraus eine
Geschäftsentscheidung abgeleitet wird.

## Geänderte Dateien

- Diese Datei (einziges committetes Artefakt dieser Task).
- Vollständiger Report inkl. aller Rohdaten/Zwischenschritte:
  `.superpowers/sdd/2026-09-16-research-bubble-kopplung/task-7-report.md`
  (nicht versioniert, `.superpowers/sdd/.gitignore` schließt das Verzeichnis aus).
- Alle Treiber-/Abgleich-Skripte liegen ausschließlich im Scratchpad
  (`E:\Temp\claude\...\scratchpad\`), nicht im Repo.

## Offene Punkte

1. Das `final_brief`/Job-ID-Mismatch (siehe oben) ist reproduzierbar für jeden, der
   dem dokumentierten Arbeitsablauf folgt — sollte in `mcp_server.py` behoben werden.
2. Keine Kostenzahl aus OpenFang verfügbar (`/api/usage` zeigt Nullen für
   `researcher-hand`) — falls Kosten-Tracking gebraucht wird, ist das eine separate
   Lücke.
3. Die in der Qualitätsbewertung benannten Lücken (Foren/Social, direkte-Wettbewerber-
   Reviews, Regulatorik-Quellen) sollten vor einer echten Positionierungsentscheidung
   für Sheerlay gegengelesen bzw. gezielt nachrecherchiert werden.
