# Design: Multi-Agent-System für automatisch gemixte DJ-Sets aus Suno-Tracks

**Datum:** 2026-08-05
**Status:** Entwurf, abschnittsweise freigegeben
**Nächster Schritt:** Implementierungsplan (writing-plans)

## 1. Ziel

Ein Kommandozeilen-Werkzeug, das aus KI-generierten Tracks ein durchgehend gemixtes DJ-Set baut.
Genre ist frei wählbar. Der Lauf ist autonom: einmal starten, fertiges Set abholen.

Ergebnis eines Laufs: eine durchgemixte Audiodatei plus Tracklist mit Zeitstempeln.

## 2. Kontext und Randbedingungen

**Suno hat keine offizielle API.** Stand 2026-08-05 läuft ein kuratiertes Partnerprogramm
(angekündigt am 2026-07-02 von CPO Jack Brody), ohne öffentliches Launch-Datum, Pricing oder
Self-Serve-Portal. Eine Anfrage über das Intake-Formular ist gestellt, eine Zusage liegt nicht vor.

**Third-Party-Wrapper sind bewusst ausgeschlossen.** Anbieter wie sunoapi.org, PiAPI oder EvoLink
generieren auf ihren eigenen Suno-Accounts. Die kommerziellen Rechte an einem Suno-Song hängen aber
am generierenden Account: wer mit aktivem Pro- oder Premier-Abo erzeugt, ist Eigentümer. Über einen
Reseller ist die Rechtekette an allem Erzeugten unklar. Dazu kommen Ausfall- und Sperrrisiko.

**Daraus folgt der Watch-Ordner.** Die Prompts schreibt das System, die Generierung macht der Nutzer
selbst in der Suno-Web-App unter eigenem Pro- oder Premier-Abo, die Downloads landen in `watch/`.
Das ist der einzige manuelle Schritt im gesamten Ablauf. Die Rechte bleiben sauber beim Nutzer.

**Der Generierungsschritt liegt hinter einer schmalen Schnittstelle**, damit ein offizieller
API-Adapter später eingehängt werden kann, ohne den Rest anzufassen. Gebaut wird nur eine
Implementierung (Watch-Ordner), keine zweite auf Vorrat.

## 3. Getroffene Entscheidungen

| Frage | Entscheidung |
|---|---|
| Zuschnitt der Agenten | Voll agentisch — jede Stufe ein eigener Agent |
| Track-Beschaffung | Watch-Ordner, manuelle Generierung bei Suno |
| Ergebnis | Durchgehender Mix als eine Datei plus Tracklist |
| Genre | Freie Eingabe, Regelwerk wird pro Lauf abgeleitet |
| Kontrollpunkte | Keine — voll autonom ab `build` |
| Sprache | Python durchgängig |
| Architektur | Sequenzielle Kette, ein Agent pro Stufe, Critic als Gate |

**Zum vollen Agentenzuschnitt:** Agenten rechnen nicht, sie beurteilen. Jeder Agent ruft seine
DSP-Tools auf und bewertet deren Ergebnis gegen das Genre-Profil. Ein Sprachmodell kann kein
Beatgrid berechnen, aber es erkennt, dass 87 BPM bei einem Drum-and-Bass-Track ein Halftime-Fehler
ist, und stößt die Erkennung mit Hinweis neu an. Dort liegt der Nutzen der agentischen Schicht.

**Zum Genre als freier Eingabe:** Statt eines festen Genre-Katalogs leitet ein Agent das Regelwerk
zur Laufzeit ab. Ein Sprachmodell weiß, wie Liquid Drum and Bass, Hard Techno oder Melodic House
klingen; eine vorab festgeklopfte Tabelle deckt immer nur ab, was beim Schreiben eingefallen ist.

## 4. Nicht-Ziele

- Kein Third-Party-Suno-Wrapper (Begründung in Abschnitt 2)
- Keine Spotify- oder SoundCloud-Anbindung. Spotify liefert kein Audio (DRM über Widevine im Web
  Playback SDK), SoundCloud untersagt Speichern und Bearbeiten in den API-Nutzungsbedingungen
- Keine Live-Steuerung, kein Echtzeit-DJing — der Mix wird gerendert, nicht gespielt
- Keine DAW-Projektdatei
- Keine grafische Oberfläche, das Werkzeug ist eine CLI
- Keine Stem-Trennung in v1 (siehe Abschnitt 10)

## 5. Architektur

Sequenzielle Kette. Zwei Kommandos, dazwischen der manuelle Suno-Schritt:

```
dj-set plan --genre "liquid dnb" --length 60   ->  profile.json, prompts.md
        [ Nutzer generiert bei Suno, Downloads nach watch/ ]
dj-set build                                    ->  set.wav, tracklist.md, qa-report.md
```

`--length` ist die Ziellänge in Minuten. `plan` legt einen neuen Run-Ordner an und schreibt seine
Run-ID auf die Standardausgabe. `build` ohne Argument nimmt den jüngsten Run, der noch kein `set.wav`
hat; mit `--run <id>` lässt sich ein bestimmter wählen.

Eingelesen werden `.mp3`, `.wav` und `.flac` aus `watch/`. Ausgegeben wird `set.wav` als
44,1 kHz / 24 Bit Stereo.

### Phase `plan`

**1. Genre-Profiler** — Leitet aus dem freien Genre-String und der Ziellänge das Regelwerk ab:
Ziel-BPM mit Toleranz, Übergangslänge in Takten, harmonische Strenge, Form der Energiekurve,
Stil-Vokabular für die Prompts, benötigte Trackzahl. → `profile.json`

**2. Prompt-Writer** — Schreibt die Suno-Prompts: Stil-Tags aus dem Vokabular, BPM-Vorgabe,
Key-Vorgaben über das Camelot-Rad verteilt, damit die Setlist später harmonischen Spielraum hat.
Fordert rund das 1,5-fache der benötigten Trackzahl an (siehe Overshoot, Abschnitt 7). → `prompts.md`

### Phase `build`

**3. Track-Analyst** — Fächert pro Datei parallel auf. Ruft die DSP-Tools (librosa, essentia) für
BPM und Beatgrid, Tonart, Energiekurve, Intro- und Outro-Grenzen, Lautheit. Prüft die Zahlen auf
Plausibilität gegen das Profil und fällt ein Urteil über die Brauchbarkeit. → `tracks/<id>.json`

**4. Curator** — Sortiert aus, was nicht passt: falsche BPM-Familie, kaputte Struktur, zu kurz, am
Genre vorbei. Jede Ablehnung mit Begründung. → `shortlist.json`

**5. Setlist-Architect** — Legt die Reihenfolge fest: Energiekurve aus dem Profil umsetzen,
harmonische Nachbarschaft wahren, keine zwei zu ähnlichen Tracks nebeneinander. Als zu ähnlich
gelten zwei Tracks, bei denen gleiche Camelot-Tonart, BPM-Differenz unter 1 und Energiedifferenz
unter 0,1 zusammentreffen — das ergibt zwar einen bequemen Übergang, aber Stillstand im Set.

**6. Transition-Designer** — Entwirft jeden Übergang: Cue-Punkte auf Downbeats, Overlap in Takten,
Technik (Blend, EQ-Swap, Filter-Sweep, Cut, Double-Drop), Timestretch je Track. → `mixplan.json`

**7. Critic** — Gate vor dem Rendern. Prüft: hält die Energiekurve, liegen alle Stretches innerhalb
der Toleranz, sitzen die Cue-Punkte auf Downbeats, stimmt die Gesamtlänge. Bei Mängeln zurück an
Stufe 5 mit konkreter Beanstandung, höchstens zwei Runden.

**8. Render-Engineer** — Führt den Mixplan aus: Timestretch über pyrubberband, Crossfades und
EQ-Automation über ffmpeg, Zusammenfügen. Trifft keine eigenen Entscheidungen. → `set.wav`,
`tracklist.md`

**9. QA** — Prüft das fertige File: Länge gegen Ziel, True-Peak und LUFS, Beat-Kontinuität an jedem
Übergang durch Nachanalyse der Nahtstellen, keine ungewollten Lücken. → `qa-report.md`

## 6. Datenfluss und Artefakte

**Ab Stufe 3 arbeitet niemand mehr auf Audio, nur noch auf Zahlen.** Der Track-Analyst ist der
einzige, der Audiodateien öffnet. Curator, Architect, Transition-Designer und Critic sehen
ausschließlich JSON. Erst der Render-Engineer öffnet wieder Audio, die QA prüft das Ergebnis.

Zwei Folgen: die kreativen Stufen sind mit Fixture-JSON testbar, ganz ohne Audiodateien. Und ein
Fehlschlag spät in der Kette kostet keine erneute Analyse.

### Ablage

```
Musik/
  djset/                  Python-Paket
  watch/                  Suno-Downloads des Nutzers
  runs/<run-id>/
    profile.json          Genre-Profiler
    prompts.md            Prompt-Writer
    tracks/*.json         Track-Analyst, eine Datei pro Track
    shortlist.json        Curator, mit Ablehnungsgründen
    mixplan.json          Transition-Designer, vom Critic freigegeben
    set.wav               Render-Engineer
    tracklist.md          Render-Engineer
    qa-report.md          QA
```

Die Run-ID hat die Form `<datum>-<genre-slug>-<laufende-nummer>`, zum Beispiel
`2026-08-05-liquid-dnb-01`.

Weil der Lauf unbeaufsichtigt stattfindet, sind diese Artefakte das einzige Fenster in die
Entscheidungen. Jede Ablehnung und jede Beanstandung liegt hinterher als Datei vor.

### Verträge

**`profile.json`**

- `genre` — die freie Eingabe, unverändert
- `bpm` — `target`, `min`, `max`
- `transition` — `bars_min`, `bars_max`, erlaubte Techniken
- `harmonic_strictness` — `strict` (gleiche Camelot-Zahl oder plus/minus 1 auf dem Rad) oder
  `loose` (plus/minus 2)
- `energy_arc` — Stützpunkte als Paare aus relativer Position (0..1) und Zielenergie (0..1)
- `track_count` — `needed` und `request` (Overshoot)
- `style_vocab` — Stil-Begriffe für die Suno-Prompts
- `structure` — `min_intro_bars`, `min_outro_bars`

**`tracks/<id>.json`**

- `id`, `path`, `duration_s`
- `bpm` — `value` und `confidence`
- `beatgrid` — `first_downbeat_s` und `bar_length_s`
- `key` — `camelot` und `confidence`
- `energy` — Kurve in 4-Takt-Schritten, Werte 0..1
- `mix_regions` — `intro_end_s`, `outro_start_s`
- `loudness` — `lufs_integrated`, `true_peak`
- `verdict` — `usable` (bool) und `notes` (Urteil des Analysten)

**`mixplan.json`**

- `target_length_s`
- `entries[]` — je Track: `track_id`, `play_from_s`, `play_to_s`, `timestretch_ratio`, `gain_db`
- `transitions[]` — je Übergang: `from`, `to`, `start_at_s` (Position in der Ausgabe-Timeline),
  `bars`, `technique`, `eq_curve`. Der `eq_curve` ist je Band (`low`, `mid`, `high`) eine Liste von
  Stützpunkten aus relativer Position im Übergang (0..1) und Gain in dB, getrennt für ein- und
  ausgehenden Track

### Determinismus-Grenze

Der Mixplan ist vollständig deterministisch: gleicher Mixplan, gleiches WAV. Der Render-Engineer
trifft keine Entscheidung mehr. Damit lässt sich neu rendern, ohne Agenten laufen zu lassen, und der
Renderer mit handgeschriebenen Mixplänen testen. Der teure, schwankende Teil (Sprachmodell-Urteile)
und der billige, verlässliche Teil (Signalverarbeitung) sind sauber getrennt.

## 7. Fehlerbehandlung

Kein Fehler darf den Lauf hängen lassen. Jeder Fehler landet im `qa-report.md`.

**Vorab-Check:** `build` zählt zuerst die Dateien in `watch/` und vergleicht mit dem Profil. Zu wenig
Material führt zum sofortigen Abbruch mit klarer Meldung, bevor der erste Agent Tokens verbraucht.

**Overshoot:** Der Prompt-Writer fordert rund das 1,5-fache der benötigten Trackzahl an. Sonst steht
der Curator vor der Wahl, Untaugliches durchzuwinken oder den Lauf zu sprengen — nachfordern kann er
nicht, weil niemand zuschaut.

| Fall | Reaktion |
|---|---|
| Einzelne Datei kaputt oder unlesbar | Analyst markiert unbrauchbar, Lauf läuft weiter |
| BPM halb oder doppelt erkannt | Analyst korrigiert gegen das Profil um Faktor 2 bzw. 0,5 und senkt die Konfidenz. Bleibt der Wert außerhalb der Toleranz, fliegt der Track raus |
| Zu wenig brauchbare Tracks | Set wird gekürzt, Vermerk im Report. Untergrenze drei Tracks, darunter harter Abbruch |
| Critic weist zweimal zurück | Bester bisheriger Mixplan wird genommen, Beanstandungen in den Report. Weder Endlosschleife noch Abbruch |
| Timestretch über plus/minus 6 Prozent nötig | Harte Grenze. Der Transition-Designer löst den Übergang anders (Cut statt Blend), sonst ändert der Architect die Nachbarschaft |
| QA findet Clipping oder Längenabweichung | Ein einziger korrigierter Re-Render, danach Auslieferung mit Vermerk |
| Fehler beim Rendern | Kein Retry. Der Renderer ist deterministisch, ein Fehler ist ein Bug. Abbruch, der Mixplan bleibt zum Reproduzieren liegen |
| Agent-Timeout oder API-Fehler | Begrenzter Retry mit Backoff |
| Agent verletzt das Ausgabeschema | Strukturierte Ausgabe erzwingen, bei Verletzung Retry mit der Fehlermeldung. Nie ungeprüftes JSON weiterreichen |

**Budget pro Lauf** für Tokens und Wanduhrzeit. Ein autonomer Lauf, der unbemerkt eskaliert, ist
schlimmer als einer, der sauber stoppt.

**`build --resume <run-id>`** — die Track-Analyse ist der teuerste Schritt. Nach einem Abbruch wird
ab dem letzten vorhandenen Artefakt weitergearbeitet statt alles neu zu analysieren.

## 8. Teststrategie

Testgetrieben, Tests vor Implementierung.

**DSP-Tools gegen synthetisches Referenzmaterial.** Ein erzeugter Click-Track mit bekanntem BPM muss
ein exakt sitzendes Beatgrid ergeben, ein erzeugter Akkord mit bekannter Tonart die richtige
Camelot-Notation. Schnell, deterministisch, ohne Netzzugriff.

**Kreative Agenten gegen Fixture-JSON.** Ein Satz erfundener Track-Analysen als Eingabe. Geprüft
werden die harten Regeln: harmonische Nachbarschaft eingehalten, Stretch-Grenze nie überschritten,
Gesamtlänge im Zielkorridor, Schema erfüllt. Nicht geprüft wird, ob die Setlist gefällt — das ist
Geschmack und kein Testfall.

**Render-Engineer mit handgeschriebenem Mixplan.** Erwartete Länge, kein Clipping, Beatgrid läuft
über die Übergänge durch.

**End-to-End-Smoke** mit vier kurzen Fixture-Tracks, echte Agenten, komplett durch. Läuft selten,
fängt aber Integrationsbrüche.

## 9. Erfolgskriterien

1. `dj-set plan --genre "liquid dnb" --length 60` erzeugt ein `profile.json` mit plausiblem
   BPM-Korridor für das genannte Genre und eine `prompts.md` mit der Overshoot-Trackzahl.
2. Nach Ablage der generierten Tracks in `watch/` läuft `dj-set build` ohne jede Interaktion durch.
3. `set.wav` liegt in der Ziellänge plus/minus 5 Prozent.
4. Die QA bestätigt durchlaufende Beat-Kontinuität an allen Übergängen.
5. True-Peak höchstens minus 1 dBTP, kein Clipping.
6. `tracklist.md` führt jeden Track mit Zeitstempel auf.
7. Jede Aussortierung und jede Critic-Beanstandung ist in den Artefakten nachvollziehbar.

## 10. Später möglich, nicht in v1

- **Judge-Panel an Stufe 5.** Mehrere Architekten mit unterschiedlichem Fokus (energiegetrieben,
  harmonisch, dramaturgisch), ein Judge wählt. Größter Qualitätshebel der Kette, kostet dort aber
  etwa das Vierfache an Tokens. Die Schnittstelle von Stufe 5 wird so gehalten, dass sich das
  nachrüsten lässt, ohne anderes anzufassen.
- **Stem-Trennung.** Suno trennt bei bezahlten Plänen in Vocals und Instrumental. Damit wären echte
  DJ-Griffe möglich, etwa eine Acapella über den nächsten Track zu legen, statt nur zu blenden.
- **Offizieller Suno-API-Adapter**, sobald der Zugang bewilligt ist. Ersetzt den manuellen Schritt,
  der Rest der Kette bleibt unverändert.
