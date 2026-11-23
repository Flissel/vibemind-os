# Abnahme Musik-Studio (Plan 1, erstes Set)

Stand: 2026-11-23. Zweig `cc/musik-studio`. Bestand unter
`MUSIK_DATEN=C:\Users\User\VibemindDaten\musik` (ausserhalb jedes Repos).

Arten der Aussagen: *measured* = von Code gemessen, niemand hat es gegengehört.
*gehört* = nur, was Felix selbst gesagt hat.

## 1. Bestand

Quelle: neun MP3s, die Felix aus seinem Suno-Konto in `Downloads` gelegt hat
(Felix 23.11.: "sonst in dwonloads"). Kopiert nach `eingang/`, aufgenommen mit
`dj-set aufnehmen --herkunft suno-eigen`. Keine Suno-Kennungen bekannt.

`The Story’s End (1).mp3` ist bytegleich mit `The Story’s End.mp3`
(gleiche SHA-256). Der Bestand hat ihn abgewiesen:
`Fehler: dasselbe Audio ist schon aufgenommen als 'the-story-s-end'`, exit 1.
`No Chicks, No Eggs (1).mp3` ist eine andere Fassung und steht als
`Variante 2` im Bestand.

Gemessene Werte, Art *measured* (Ausgabe von `dj-set bestand`). Niemand hat
Tempo oder Tonart gegengehört.

| ID | Titel | Tempo (BPM) | Tonart (Camelot) | Länge |
|---|---|---|---|---|
| farewell-kiss | farewell kiss | 94.1 | 3A | 2:25 |
| love-s-just-a-game | Love's just a Game | 120.0 | 10B | 3:25 |
| mind-makers | Mind makers | 121.2 | 8B | 2:28 |
| no-chicks-no-eggs | No Chicks, No Eggs | 85.9 | 7A | 3:53 |
| no-chicks-no-eggs-variante-2 | No Chicks, No Eggs (Variante 2) | 119.6 | 8A | 3:31 |
| smaragd-green-eyes | Smaragd Green Eyes | 94.0 | 4A | 3:14 |
| so-and-so | So and so | 142.7 | 3B | 3:41 |
| the-story-s-end | The Story’s End | 146.4 | 6A | 4:10 |

Tempo-Gruppen (Bereich ±6 %): um 120 BPM drei Songs, um 90 BPM drei Songs
(85.9, 94.0, 94.1), um 145 BPM zwei Songs.

## 2. Vorprüfung 20 Minuten

`dj-set set --wunsch "Abend mit Freunden, erst ruhig, dann mehr Energie" --length 20`

```
Fehler: die 3 Songs im Tempo-Bereich 119.6 bis 121.2 BPM ergeben zusammen 9.4 Minuten,
nach Abzug von je 16 Takten Uebergang spielbar 7.8 Minuten, gewuenscht sind 20.0 Minuten.
```

exit 1 nach 3,5 s, vor jedem Modellaufruf, kein Laufordner. Das ist das
gewollte Verhalten (Plan Task 7 Step 4: nicht still kürzen). Für 20 Minuten
in einem Tempo-Bereich braucht der Bestand mehr Songs; das entscheidet Felix.

## 3. Erster Lauf (7 Minuten, Kettentest)

Um die ganze Kette einmal echt zu prüfen, lief derselbe Wunsch mit
`--length 7` (passt zu den drei Songs um 120 BPM). Das ersetzt nicht das
20-Minuten-Set.

Modellprüfung vorher: ein echter Aufruf über `ClaudeClient` mit
`MODEL = "claude-opus-5"` antwortete in 33 s mit gültigem JSON.

### Lauf 1 — `2026-11-23-abend-mit-freunden-erst-ruhig-dann-mehr-01`, exit 2

7,1 Minuten Laufzeit. Bereich 119.6–121.2 BPM (Ziel 120.0), drei Songs:
`no-chicks-no-eggs-variante-2` → `mind-makers` → `love-s-just-a-game`.
Set gerendert (`set.wav`, `set.mp3`), 8,9 Minuten.

Critic ließ zwei Beanstandungen offen (Länge 8.9 statt 7 min; Energie an
Position 3 bei 0.55 statt etwa 0.95). Beides folgt aus nur drei Songs.

QA, blockierend (*measured*):

```
Befund (seam): An der Naht von no-chicks-no-eggs-variante-2 nach mind-makers bei 3:26
stimmt das Timing nicht: ein Intervall um die Naht weicht 0.04 Schlaege vom naechsten
ganzen Schlag ab, gemitteltes Tempo 114.7 BPM, erwartet waren 120.0 BPM ...
```

**Fund 1: der Gesamtwert eines Songs stand nicht für den Song.** Ursache
mit systematic-debugging gesucht, nicht geraten:

- Nahtfenster getrennt gemessen: vor der Naht 90.4 BPM, danach 114.7,
  32 s vor dem Fenster 176.6 (≈ 2 × 88). Die zweite Naht im selben Set:
  120.9 / 120.5 / 120.1 — sauber.
- Quelle `no-chicks-no-eggs-variante-2.wav` ungerendert: 180–210 s
  88.8 BPM, 100–140 s 87.4 BPM.
- Alle acht Songs in 30-s-Abschnitten:

| Song | Gesamt | Abschnitte |
|---|---|---|
| farewell-kiss | 94.1 | 93, 93, 94, 94 |
| love-s-just-a-game | 120.0 | 120, 120, 119, 119, 119, 120 |
| mind-makers | 121.2 | 118, 121, 122, 122 |
| no-chicks-no-eggs-variante-2 | 119.6 | **119, 88, 89, 119, 119, 119, 88** |
| no-chicks-no-eggs | 85.9 | 120, 87, 87, 87, 87, 86, 87 |
| smaragd-green-eyes | 94.0 | 95, 95, 93, 95, 93, 94 |
| so-and-so | 142.7 | 144, 143, 142, 142, 143, 143, 144 |
| the-story-s-end | 146.4 | 148, 147, 147, 146, 147, 147, 148, 146 |

Die Beat-Erkennung springt in Variante 2 zwischen zwei Ebenen im
Verhältnis 4:3 (89.7 × 4/3 = 119.6). Der Gesamtwert mittelt über beide.
Welche Ebene musikalisch stimmt, hat niemand gehört; die Originalfassung
desselben Songs liegt bei 86–87, das spricht für ~88.

Die QA hat also richtig geblockt. Der Fehler lag davor: der Bestand kannte
nur den Gesamtwert.

Behoben in `7ea75296` (Test zuerst, 491 + 25 grün): der Bestand misst
zusätzlich das Tempo je 30-s-Abschnitt. Ein Song, bei dem weniger als drei
Viertel der Abschnitte zu seinem Gesamttempo passen, kommt nicht in die
Tempo-Wahl. Er wird in `aufnehmen`, `set`, `tempo.json` und `qa-report.md`
als „Tempo unsicher“ benannt. Die Schwelle liegt zwischen den gemessenen
57 % (Variante 2) und 86 % (Original mit abweichendem Intro).

### Lauf 2 — `...-02`, exit 1

Derselbe Wunsch, `--length 7`, mit `7ea75296`. 1,6 Minuten, Profil und drei
Analysten-Aufrufe bezahlt, dann Abbruch vor dem Rendern.

- Die Abschnitte aller acht alten Einträge wurden nachgemessen. Ausgabe:
  `Tempo unsicher: no-chicks-no-eggs-variante-2 (119.6 BPM, Abschnitte 88 bis 119 BPM), bitte gegenhoeren`.
  Fund 1 ist damit am echten Bestand behoben.
- Bereich jetzt 85.9–94.1 BPM (Ziel 91.0), drei Songs: `farewell-kiss`,
  `no-chicks-no-eggs`, `smaragd-green-eyes`.
- Der Analyst verwarf `no-chicks-no-eggs`: „es gibt praktisch kein Outro:
  der Outro-Start 232.9 s ist identisch mit der Trackdauer“. Danach
  `Fehler: Nach dem Aussortieren bleiben 2 Tracks, mindestens 3 werden gebraucht.`

**Fund 2: die Kette mischt nur in einem gemessenen Outro, ganze Songs
haben keins.** `find_mix_regions` setzt den Outro-Start auf das Ende des
letzten Abschnitts, der höchstens 6 dB unter der Spitze liegt. Felix'
Suno-Songs bleiben bis zum Schluss laut: Outro `no-chicks-no-eggs` 0 s,
`smaragd-green-eyes` 0,3 s. Der Designer legt jeden Übergang in diesen
Bereich (`_snapped_outro`), und der Analyst-Prompt fragt ausdrücklich, „ob es
überhaupt ein Intro und ein Outro zum Mixen gibt“. Beides passt zu
DJ-Tracks (Weg `build`/watch), nicht zu fertigen Songs:

- Lauf 1: der Übergang `variante-2 → mind-makers` dauerte 1 Takt (2,0 s,
  `eq_swap`), weil vom Outro nur das letzte Stück übrig war.
- Lauf 2: ein Song ganz verworfen. Ein anderer mit 0,3 s Outro wurde
  durchgelassen. Das Urteil ist also auch uneinheitlich.

Ein DJ blendet bei einem Song ohne Outro 16–32 Takte vor dem Ende über
laufende Musik. Felix 23.11.: „Ja, so bauen“.

Behoben in `d17209a1` (Test zuerst, 498 + 25 grün): `dsp.analyze.mischbereich`
gibt jedem Ende mindestens 16 Takte, bei sehr kurzen Tracks ein Drittel. Ein
längeres gemessenes Intro oder Outro bleibt. Die Messung selbst bleibt
unverändert. Designer, Analyst und Curator arbeiten mit dem Mischbereich. Ein
fehlendes leises Intro oder Outro ist im Prompt kein Aussortiergrund mehr.

### Lauf 3 — `...-03`, exit 2

Derselbe Wunsch mit `d17209a1`. `--length 8` stoppte die Vorprüfung
(spielbar 7,4 Minuten), also `--length 7`. 3,7 Minuten Laufzeit.

- Bereich 85.9–94.1 BPM (Ziel 91.0). Alle drei Songs im Set, keiner
  aussortiert: `no-chicks-no-eggs` → `farewell-kiss` → `smaragd-green-eyes`.
- Beide Übergänge mit 16 Takten (`filter_sweep` am Tonart-Bruch 7A→3A,
  dann `eq_swap`). QA: „Keine verkürzten Übergänge“. Fund 2 ist damit am
  echten Bestand behoben.
- Set 7:59 (`set.mp3`), −15.1 LUFS, True Peak −1.3 dB nach einer
  Pegelkorrektur der QA.
- Hinweis Länge: 8.0 statt 7 Minuten. Mit drei Songs gibt das Material
  nicht weniger her, ohne einen Song zu kürzen.

QA, blockierend (*measured*):

```
Befund (seam): An der Naht von no-chicks-no-eggs nach farewell-kiss bei 2:54
stimmt das Timing nicht: ein Intervall um die Naht weicht 0.07 Schlaege vom
naechsten ganzen Schlag ab, gemitteltes Tempo 91.4 BPM, erwartet waren
91.0 BPM ohne Abweichung ueber 0.05 Schlaege.
```

Anders als Lauf 1 (114.7 statt 120 BPM) liegt das gemittelte Tempo hier
richtig. Die Abweichung ist ein einzelnes Intervall, 0,07 Schläge sind bei
91 BPM etwa 46 ms. Die Naht bei 4:39 ist laut Messung sauber.

*gehört* — Felix 23.11. zur Naht bei 2:54: „Stolpert hörbar“.

**Fund 3: ein Song hat nicht überall dasselbe Tempo.** Ursache gemessen,
nicht geraten. Für jeden Mischbereich eine Ausgleichsgerade durch die
erkannten Beats gelegt (*measured*):

| Song | Bereich | Tempo gesamt | Tempo lokal | Phase gg. Raster | Streuung nach lokaler Geraden |
|---|---|---|---|---|---|
| no-chicks-no-eggs | Outro 186–233 s | 85.88 | 85.89 | +0.01 | 0.02 |
| farewell-kiss | Intro 0–41 s | 94.06 | **93.04** | −0.08 | 0.03 |
| farewell-kiss | Outro 102–145 s | 94.06 | **95.07** | +0.11 | 0.30 |
| smaragd-green-eyes | Outro 153–194 s | 93.97 | 94.25 | **−0.45** | 0.11 |
| mind-makers | Intro 1–33 s | 121.16 | 119.70 | −0.03 | 0.23 |
| love-s-just-a-game | Intro / Outro | 119.98 | 120.00 / 120.01 | +0.12 / +0.06 | 0.05 / 0.02 |

`farewell-kiss` wird über den Song schneller, von 93.0 im Intro auf 95.1
im Outro. Die Kette streckt jeden Song mit einem
Verhältnis aus dem Gesamttempo (91.0 / 94.06). Das Intro läuft damit bei
90.0 statt 91.0 BPM, 1,1 % zu langsam. Über die 64 Schläge des Übergangs
wandern die beiden Spuren um etwa 0,7 Schläge auseinander. Das ist das
Stolpern. Die QA misst nur ein kurzes Fenster an der Naht und sah davon
0,07 Schläge.

Dazu kommt: das Raster ist eine einzige Gerade ab dem ersten Beat. Wo ein
Song driftet, liegen die Cue-Punkte daneben (`smaragd-green-eyes` am Ende
fast einen halben Schlag). Songs mit stabilem Tempo (`love-s-just-a-game`,
das Outro von `no-chicks-no-eggs`) sind sauber.

Felix 23.11.: „Abschnittsweise strecken“.

Behoben in `4f627593` (Test zuerst, 516 + 25 grün):

- `dsp.analyze.lokale_raster_messen` legt je Mischbereich eine eigene
  Ausgleichsgerade durch die Beats (Tempo, Downbeat, Streuung) und
  speichert sie in der Messung. Ältere Songs im Bestand misst `dj-set set`
  beim ersten Lauf nach.
- Der Designer setzt Cue-Punkte auf das lokale Raster und teilt jeden
  Eintrag in Streckabschnitte. Das Intro wird mit dem Verhältnis aus dem
  lokalen Intro-Tempo gestreckt, der Mittelteil mit dem Gesamttempo, das
  Outro mit dem lokalen Outro-Tempo. So läuft jeder Mischbereich genau im
  Ziel-Tempo.
- Der Renderer streckt die Abschnitte einzeln und fügt sie mit 10 ms
  Überblendung zusammen. Ein Klick-Test zeigt: die Schläge liegen auf
  3 ms genau dort, wo der Plan sie erwartet.
- Die Phasenprüfung des Kritikers rechnet mit dem lokalen Raster.
- Der QA-Report bekommt den Abschnitt „Unruhige Beats im Mischbereich“, wenn
  eine Streuung über 0,1 Schlägen liegt. Das betrifft heute das Outro von
  `farewell-kiss` (0,30), das Intro von `mind-makers` (0,23) und das Outro
  von `smaragd-green-eyes` (0,11). Dort kann die Kette das Tempo nicht
  genauer treffen, als der Song es selbst hält.

### Lauf 4 — erster Versuch, exit 1

Mit `4f627593`, gleicher Wunsch, `--length 7`. Die lokalen Raster wurden für
alle acht Songs nachgemessen. Danach:
`Fehler: Nach dem Aussortieren bleiben 1 Tracks, mindestens 3 werden gebraucht.`

**Fund 4: der Analyst sortierte aus, was er nicht aussortieren sollte.**

- `farewell-kiss`: „BPM 94.1 liegt knapp ueber der Obergrenze 94.06“. Das
  Tempo ist 94.0619 und damit genau die Grenze. Der Prompt zeigte es auf
  eine Stelle gerundet neben der ungerundeten Grenze.
- `no-chicks-no-eggs`: abgelehnt wegen der Tonart 7A unter dem strengen
  Profil. Ob zwei Tonarten passen, hängt aber vom Nachbarn ab. Das prüft die
  Reihenfolge-Stufe an jeder Naht, und in Lauf 3 hat der Übergang 7A→3A
  mit `filter_sweep` funktioniert.

Behoben in `de82c71d` (Test zuerst, 519 + 25 grün). Analyst und Curator
zeigen Tempo und Grenzen mit zwei Stellen. Der Analyst bekommt die
nachgerechnete Zeile „Tempo im Korridor: ja/nein“. Eine Tonart allein ist
kein Aussortiergrund mehr.

### Lauf 4 — `...-05`, exit 2

Mit `de82c71d`, gleicher Wunsch, `--length 7`, 3,5 Minuten Laufzeit.

- Alle drei Songs im Set, keiner aussortiert. Reihenfolge diesmal
  `no-chicks-no-eggs` → `smaragd-green-eyes` → `farewell-kiss`.
- Beide Übergänge mit 16 Takten, Set 7:54, −15.2 LUFS, True Peak −1.3 dB.
- QA blockierend: wieder ein Intervall an der Naht bei 4:20
  (`farewell-kiss` → `no-chicks-no-eggs`) mit 0,07 Schlägen. Der neue
  Abschnitt „Unruhige Beats“ nennt genau diese Naht (Outro `farewell-kiss`
  0,30, Intro `no-chicks-no-eggs` 0,25) und die bei 2:37 (Outro
  `smaragd-green-eyes` 0,11).
- Hinweis Länge wie in Lauf 3: 7,9 statt 7 Minuten. Mit drei Songs geht es
  nicht kürzer.

Versatz der erkannten Beats beider Spuren über den ganzen Übergang, in
Schlägen bei 91 BPM (*measured*, `scratchpad/naht_versatz.py`):

| Lauf | Naht | Median | Max |
|---|---|---|---|
| 3 | 2:54 `no-chicks-no-eggs` → `farewell-kiss` | 0,26 | 0,56 |
| 3 | 4:39 `farewell-kiss` → `smaragd-green-eyes` | 0,18 | 0,44 |
| 4 | 2:37 `smaragd-green-eyes` → `farewell-kiss` | **0,05** | 0,38 * |
| 4 | 4:20 `farewell-kiss` → `no-chicks-no-eggs` | 0,30 | 0,50 |

\* Ohne den letzten Schlag, der nach dem letzten erkannten Beat von
`smaragd-green-eyes` liegt (dort 1,28, Messartefakt). Die 0,38 gelten für
die letzten vier Schläge, in denen `smaragd-green-eyes` schon fast
ausgeblendet ist.

Zwei Befunde:

- Wo der Song sein Tempo hält, wirkt das abschnittsweise Strecken: die
  Naht bei 2:37 liegt im Median bei 0,05 Schlägen (rund 30 ms). In Lauf 3
  waren es 0,18 bis 0,26.
- Die Naht bei 4:20 liegt in den beiden unruhigsten Bereichen des Bestands.
  Dort streut schon die Beat-Erkennung um 0,25 bis 0,30 Schläge. Die
  Messung kann dort also nicht unterscheiden, ob die Spuren auseinanderlaufen
  oder nur die Erkennung wackelt. Entscheiden muss das Ohr.

*gehört* — offen, Felix hört beide Nähte.
