# Design: Musik-Studio — der Musik-Space liefert fertige Musik

**Datum:** 2026-11-23
**Status:** Entwurf, wartet auf Felix' Freigabe
**Nächster Schritt:** Umsetzungsplan (writing-plans)
**Umfang:** Teil A von zwei. Teil B (das Live-Pult fertig bauen) bekommt ein eigenes Design, sobald A steht.

## 1. Wozu

Felix, 23.11.: Der Musik-Space ist dafür da, dass Musik **entsteht**, wenn VibeMind welche
braucht: Marketingmusik, Filmmusik, Musik passend zu einem Video, ein Set für eine ganze Party
oder ein Event. „Stell dir vor, als wäre das ein AI-DJ." Bedient wird er von einem Agenten
(OpenClaw oder Hermes), wie die anderen Spaces. Testmaterial sind Felix' eigene Songs aus Suno.

Der heutige Space kann das nicht. Er steuert ein Echtzeit-Pult (Rust-Engine `Flissel/Musik`),
fährt genau einen Übergang und liefert keine Datei.

## 2. Was es schon gibt

| Baustein | Ort | Stand |
| --- | --- | --- |
| Live-Pult, Agentenseite | `vibemind-os/spaces/musik/` | Mix-Engineer, 25 Tests, ein Übergang am 03.09. gemessen |
| Live-Pult, Engine | `Flissel/Musik`, PR #1 (Draft, mergebar, Checks grün) | Brücke mit 16 Werkzeugen und Freigabe-Gatter |
| `djset` | `C:\Users\User\Desktop\Musik` (lokales Git-Repo, kein Remote, letzter Commit 25.08.) | rendert aus Tracks ein gemixtes Set als Datei |

`djset` ist der Baustein, der zu Abschnitt 1 passt. Es ist eine Python-Kette aus sieben Stufen:
Analyse (BPM, Tonart, Energie, Intro/Outro, Lautheit), Auswahl, Reihenfolge, Übergänge,
Prüfer, Rendern, Qualitätsprüfung. Ergebnis: `set.wav`, `tracklist.md`, `qa-report.md`.
Die Urteile fällt ein Sprachmodell über `claude-agent-sdk`, also über das Claude-Abo.

Gemessen am 23.11.:

- `djset` startet nicht: `ModuleNotFoundError: pyloudnorm`. Die Abhängigkeiten sind in keiner
  Umgebung installiert.
- Es gibt genau einen Lauf (`runs/2026-08-21-liquid-dnb-01`), und der hat nur `profile.json`
  und `prompts.md`. **Ein Set ist nie gerendert worden.**
- `djset` kennt nur einen Einstieg: Genre nennen, Suno-Prompts schreiben lassen, neue Tracks
  erzeugen, dann bauen. „Bau ein Set aus den Songs, die schon da sind" gibt es nicht.
- Es gibt keinen Zuschnitt auf eine feste Länge und keine Anbindung an VibeMind.

## 3. Ziele

1. **Party-Set:** aus Songs im Bestand ein durchgemixtes Set gewünschter Länge als eine Datei,
   mit Tracklist.
2. **Musik zum Video:** ein Musikstück mit sekundengenauer Länge, das sauber anfängt und
   sauber endet, wahlweise direkt unter ein Video gelegt.
3. **Ein Agent bedient das.** Ein Mensch sagt im Chat, was er braucht; der Agent ruft
   Werkzeuge und meldet die fertige Datei.
4. **Ein gehörtes Ergebnis.** Fertig ist Teil A, wenn Felix ein Set und eine Video-Musik aus
   seinen eigenen Songs angehört hat.

## 4. Nicht-Ziele

- Keine automatische Erzeugung neuer Songs. Suno hat keine öffentliche Schnittstelle; neue
  Songs erzeugt Felix selbst in Suno. Die vorhandene `plan`-Stufe von `djset` (Prompts
  schreiben) bleibt nutzbar, ist aber nicht Teil der Abnahme.
- Kein Live-Auflegen. Das ist Teil B.
- Keine eigene Oberfläche in v1. Bedient wird über den Agenten.
- Keine Stem-Trennung, keine Musik aus Spotify, YouTube oder SoundCloud.
- Kein Betrieb auf der VM. Gerendert wird am PC.

## 5. Was nie passieren darf

- **Fremde Musik im Bestand.** In den Bestand kommen nur Songs, die Felix selbst erzeugt hat
  oder an denen die Rechte geklärt sind. Jeder Song trägt seine Herkunft. Ein Song ohne
  Herkunft wird nicht gemischt.
- **Ein Ergebnis, das als „fertig" gemeldet wird, obwohl die Qualitätsprüfung rot ist.** Der
  Agent meldet dann das Ergebnis der Prüfung, nicht die Datei.
- **Eine Zahl aus der Analyse als gehörte Wahrheit ausgeben.** Die Beweis-Disziplin des Space
  (`STATUS.md`: measured / static / configured / open) gilt weiter.

## 6. Wege, die zur Wahl standen

| Weg | Was | Abwägung |
| --- | --- | --- |
| A | `djset` wird der Kern des Space, als Werkzeuge für einen Agenten | kürzester Weg zum Ziel, rendert schneller als Echtzeit, läuft unter Windows |
| B | Live-Pult fertig bauen, Sets darüber aufnehmen | Aufnahme in Echtzeit, Probelauf nur unter Linux, kein Video-Zuschnitt |
| C | A, danach B | doppelter Aufwand |

**Felix hat C gewählt** (23.11.). Reihenfolge: A zuerst, weil A das Ziel aus Abschnitt 1
erreicht; B danach mit eigenem Design. Dieses Dokument beschreibt A.

## 7. Aufbau

```text
Mensch im Chat
   │
   ▼
Musik-Claw (Agent, nach dem Muster des Video-Claw)
   │  MCP
   ▼
Werkzeug-Dienst „musik-studio" (am PC)
   │  legt Aufträge an, liest Ergebnisse
   ▼
Auftrags-Läufer (ein Auftrag zur Zeit)
   │
   ▼
djset: Analyse → Auswahl → Reihenfolge → Übergänge → Prüfer → Rendern → Qualitätsprüfung
   │
   ▼
Datenordner: bestand/  auftraege/<id>/  (außerhalb des Repos)
```

### 7.1 Wo der Code liegt

`djset` zieht nach `vibemind-os/spaces/musik/studio/djset/` um, mit seiner Git-Historie
(`git subtree add` aus dem lokalen Repo). `C:\Users\User\Desktop\Musik` bleibt unangetastet
liegen. Grund: Der Code hat heute kein Remote und liegt nur auf einer Platte; im Space ist
er versioniert, gespiegelt und neben dem, was ihn benutzt.

Der bisherige Inhalt von `spaces/musik/` (Live-Pult: `agents/`, `api/`, `acceptance/`)
bleibt, wo er ist. Das Studio kommt daneben:

```text
vibemind-os/spaces/musik/
  agents/ api/ acceptance/ docs/      Live-Pult (unverändert, Teil B)
  studio/
    djset/                            die Kette (umgezogen)
    bestand.py                        Songs aufnehmen, Herkunft führen
    auftraege.py                      Auftrag anlegen, Zustand, Ergebnis
    zuschnitt.py                      Musik auf feste Länge
    werkzeuge.py                      MCP-Dienst
    laeufer.py                        arbeitet Aufträge ab
    tests/
    requirements.txt
```

Die 25 vorhandenen Tests bleiben ohne Zusatzpakete lauffähig. Die Studio-Tests brauchen
`librosa` und laufen in einer eigenen Umgebung (`spaces/musik/studio/.venv`), nicht im
schlanken CI-Schritt.

### 7.2 Bestand

Ein Ordner außerhalb des Repos (Umgebungsvariable `MUSIK_DATEN`). Je Song eine Audiodatei
und ein Eintrag mit: Titel, Herkunft (`suno-eigen`, `eigen-aufgenommen`, `lizenz:<quelle>`),
Suno-Kennung falls vorhanden, Datum der Aufnahme, und die Analyse von `djset` (wird einmal
gerechnet und aufgehoben, nicht bei jedem Auftrag neu).

Erstbefüllung: Felix' Songs aus seinem Suno-Konto, heruntergeladen über seinen Browser
(Claude in Chrome), jeweils erst nach seinem Ja zum konkreten Download.

### 7.3 Zwei Auftragsarten

**`set`** — Eingabe: Länge in Minuten, Anlass oder Stimmung in freien Worten, optional eine
Auswahl von Songs oder ein Verlauf („ruhig anfangen, nach der Hälfte Druck"). `djset` bekommt
einen neuen Einstieg „aus dem Bestand": Das Regelwerk (Tempo-Bereich, Übergangslänge,
Energieverlauf) wird aus dem Wunsch **und dem vorhandenen Material** abgeleitet, statt neue
Songs zu bestellen. Reicht der Bestand für die Länge nicht, sagt der Auftrag das vor dem
ersten Sprachmodell-Aufruf und nennt, was fehlt.

**`video`** — Eingabe: Länge in Sekunden oder eine Videodatei (dann wird die Länge
gemessen), Stimmung, optional Zeitpunkte, an denen etwas passieren soll (Schnitt, Höhepunkt).
Ergebnis: ein Stück genau dieser Länge. Regeln:

- Anfang und Ende liegen auf Taktgrenzen des Materials; die Länge wird über die Wahl der
  Abschnitte und ein kleines Tempo-Strecken getroffen (innerhalb der Toleranz, die `djset`
  schon kennt), nicht durch hartes Abschneiden.
- Das Ende ist ein Schluss: letzter Takt klingt aus, kurze Ausblende.
- Lautheit auf einen Zielwert (Vorgabe −14 LUFS, für Sprache darunter einstellbar).
- Kurze Stücke (unter etwa einer Minute) kommen aus **einem** Song, längere dürfen mischen.
- Optionaler letzter Schritt `unter_video_legen`: `ffmpeg` legt die Musik unter das Video und
  schreibt eine neue Datei; das Original bleibt.

### 7.4 Werkzeuge des Agenten

| Werkzeug | Tut |
| --- | --- |
| `bestand_zeigen` | Songs mit Tempo, Tonart, Länge, Herkunft |
| `song_aufnehmen` | Datei in den Bestand, mit Herkunft; stößt die Analyse an |
| `set_beauftragen` | legt einen `set`-Auftrag an |
| `videomusik_beauftragen` | legt einen `video`-Auftrag an |
| `auftrag_zeigen` | Zustand, Tracklist, Ergebnis der Qualitätsprüfung, Pfad der Datei |
| `unter_video_legen` | Musik eines fertigen Auftrags unter ein Video |
| `auftrag_abbrechen` | bricht einen laufenden Auftrag ab |

Der Agent bekommt **kein** Shell- und kein Dateiwerkzeug (Hard Rule 1 des Space gilt auch
hier). Aufträge laufen im Hintergrund; ein Werkzeugaufruf kehrt sofort zurück, weil die
Claude-CLI stille MCP-Aufrufe nach rund 300 s abbricht (Erfahrung aus dem Video-Claw).

### 7.5 Der Agent

Ein „Musik-Claw" nach dem Muster des Video-Claw: Claude über eine eigene Shim mit genau
diesem Werkzeug-Dienst, eigenes Tagesbudget im Claude-Budget-Wächter. Die Kette selbst
(`djset`) ruft das Sprachmodell über `claude-agent-sdk` und zählt gegen dasselbe Budget.
Ports nach der Port-Hygiene des Repos; der Dienst wird beim OpenFang-Hub angemeldet, damit
andere Agenten (Marketing, Video-Claw) Musik bestellen können.

### 7.6 Fehler

Jeder Auftrag endet in genau einem von drei Zuständen: `fertig` (Qualitätsprüfung grün),
`fertig_mit_befund` (Datei da, Prüfung rot, Befund genannt), `gescheitert` (Grund genannt).
Kein Auftrag bleibt stumm hängen: Der Läufer setzt ein Zeitlimit je Stufe.

## 8. Abnahme

| | Schritt | Fertig, wenn |
| --- | --- | --- |
| 1 | `djset` läuft im Space | seine Tests laufen in `studio/.venv` grün |
| 2 | Bestand | mindestens acht von Felix' Suno-Songs sind aufgenommen und analysiert |
| 3 | Erstes Set | ein Set von rund 20 Minuten ist gerendert, Qualitätsprüfung grün |
| 4 | Erste Video-Musik | ein Stück von 30 s trifft die Länge auf ±50 ms und endet als Schluss |
| 5 | Agent | beide Aufträge wurden über den Musik-Claw im Chat bestellt, nicht per Kommandozeile |
| 6 | Gehört | Felix hat beide Ergebnisse angehört und gesagt, was nicht passt |

Schritt 3 ist der erste echte Lauf von `djset` überhaupt. Es ist damit zu rechnen, dass er
Fehler findet, die keine Testsuite hatte (so war es auch beim Live-Pult und beim Video-Claw).

## 9. Meine Festlegungen (nicht von Felix entschieden)

- Der Code zieht in den Space um, statt ein eigenes GitHub-Repo zu bekommen.
- Agent nach dem Video-Claw-Muster (OpenClaw-Linie), nicht Hermes.
- Keine Oberfläche in v1.
- Bestand als Ordner mit Einträgen, keine Datenbank.
- Vorgabe −14 LUFS; Ausgabe `wav`, zusätzlich `mp3` zum Verschicken.
- Kurze Video-Musik aus einem einzelnen Song.
- Gearbeitet wird in einem eigenen Worktree (`cc/musik-studio`), weil der Haupt-Checkout von
  `vibemind-os` der PC-Betrieb ist.

Nach dem Gegenlesen ergänzt (gemessen am Code von `djset`):

- **Stereo ist Pflicht.** `djset` rendert heute durchgehend mono (`render/engine.py`,
  `_load_mono`). Für Marketing- und Filmmusik reicht das nicht. Der Umbau von Rendern,
  Strecken und Klangregelung auf Stereo kommt **vor** dem ersten angehörten Ergebnis.
- **Was die Qualitätsprüfung rot macht.** Heute macht jeder Befund den Bericht rot. Künftig
  blockieren nur: Lücke, Übersteuerung, gemessener Taktbruch an einer Naht. Eine nicht
  messbare Naht und eine Länge außerhalb ±5 % beim Set sind Hinweise im Bericht, kein Rot.
  Bei Video-Musik bleibt die Länge hart (±50 ms).
- **Aufnahme in den Bestand nur aus einem Eingangsordner.** `song_aufnehmen` nimmt einen
  Dateinamen aus `MUSIK_DATEN/eingang/`, keinen freien Pfad. In den Eingang legt nur Felix
  (oder ich nach seinem Ja zum Download). Das Werkzeug gehört nur dem Musik-Claw; über den
  OpenFang-Hub sind für andere Agenten nur `bestand_zeigen`, die beiden Bestell-Werkzeuge
  und `auftrag_zeigen` sichtbar.
- **Zeitpunkte im Video sind Wunsch, nicht Zusage.** Länge und sauberer Schluss gehen vor.
  Der Bericht nennt je Zeitpunkt die erreichte Abweichung.
- **`unter_video_legen` mischt unter den Originalton** (Pegel einstellbar), mit Schalter
  „Originalton ersetzen". Kein automatisches Absenken unter Sprache in v1.
- **Eigenes Budget für die Kette.** Ein Set kostet grob 15–20 Sprachmodell-Aufrufe. Die Kette
  bekommt einen eigenen Eintrag im Budget-Wächter, getrennt vom Chat des Musik-Claw.

### Tempo im Set — entschieden

**Felix, 23.11.: ein Tempo-Bereich je Set.** Songs außerhalb werden für dieses Set
aussortiert und im Ergebnis genannt. Das Wandern durch Tempi ist ein möglicher Nachzug,
nicht Teil von A. Zur Wahl stand:

**Tempo im Set.** `djset` plant ein Set um **ein** Zieltempo und streckt Songs höchstens um
±6 %. Was weiter weg liegt, passt nicht in dasselbe Set. Felix' Lieblingssongs sind nicht für
ein gemeinsames Tempo erzeugt worden.

- *Ein Tempo-Bereich je Set:* Songs außerhalb werden für dieses Set aussortiert und genannt.
  Kleiner Umbau, Abnahme-Schritt 3 hängt dann davon ab, dass genug Songs nah beieinander
  liegen.
- *Set wandert durch Tempi:* Blöcke mit eigenem Tempo und Wechsel dazwischen (harter Schnitt
  oder Tempofahrt). Übergänge, Prüfer und Qualitätsprüfung müssen dafür umgebaut werden.

## 10. Dem Bauenden überlassen

- Ob der „aus dem Bestand"-Einstieg den vorhandenen Genre-Profiler erweitert oder eine
  eigene Stufe wird.
- Wie `zuschnitt.py` die Abschnitte wählt (die Analyse liefert Energie in 4-Takt-Schritten
  und Intro-/Outro-Grenzen).
- Ob der Auftrags-Läufer ein eigener Prozess ist oder im Werkzeug-Dienst mitläuft.
- Form der Shim (welche vorhandene Hülle die Basis ist) und die konkreten Ports.

## 11. Teil B, nur als Merkposten

Engine-PR `Flissel/Musik#1` mergen (Felix' Entscheidung), Manifest-Eintrag, Track-Wähler,
Set-Planer, Guardrail, die vier ungespielten Übergänge, Probelauf unter WSL mit gedrosseltem
Cargo-Build. Der Bestand und die Analyse aus Teil A sind dafür die Sammlung.
