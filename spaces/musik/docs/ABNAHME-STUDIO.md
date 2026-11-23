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
laufende Musik. Ob die Kette das für Läufe aus dem Bestand so machen soll,
entscheidet Felix (offen).
