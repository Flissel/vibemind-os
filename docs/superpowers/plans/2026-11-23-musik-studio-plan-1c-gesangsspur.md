# Musik-Studio Plan 1c: Gesangsspur

Felix 23.11. nach Lauf 4: "Bei solch stark unterschiedlichen Texten bzw.
Musikstilen bringt das Overlay nicht so viel, weil die Texte sich gegenseitig
aufessen. Wir muessten den Text auf einer eigenen Spur haben, sodass wir den
an- und ausmachen koennten." Entscheidung: "Lokal trennen".

## Probe (gemessen 23.11.)

`htdemucs` (Demucs 4.0.1, MIT) auf der CPU, torch 2.5.1+cpu, 24 Threads:
`no-chicks-no-eggs` 232,9 s in 89 s getrennt, Modell laden 7 s (80 MB,
einmalig). Stimme + Begleitung = Song bis auf −46 dB (Song −14,9 dB RMS).
GPU und ComfyUI bleiben unberuehrt.

## Regeln

1. Jeder Song im Bestand bekommt `<id>.stimme.wav` und `<id>.begleitung.wav`.
   Begleitung ist die Summe aus Drums, Bass und Other. Aufnehmen trennt mit;
   `set` holt es fuer aeltere Songs nach. Fehlt Demucs, laeuft alles wie
   bisher, der Bericht sagt es.
2. Im Uebergang singt hoechstens eine Stimme. Die Stimme des ausgehenden Songs
   blendet im Takt vor dem Wechsel aus, die des eingehenden im Takt danach
   ein. Der Wechsel liegt auf der Taktgrenze nahe der Mitte des Uebergangs.
   Die Begleitung blendet wie bisher ueber den ganzen Uebergang.
3. Gesang an/aus fuer das ganze Set: `Profile.gesang` (der Profiler setzt es
   aus dem Wunsch, z. B. "instrumental"), `--ohne-gesang` erzwingt aus.
   Ohne Gesang braucht jeder Song eine getrennte Stimme, sonst Abbruch vor
   dem ersten Modellaufruf.
4. Der Renderer entscheidet nichts: Wechselzeit und Blende stehen im Mixplan
   (`Transition.stimmwechsel`), an/aus je Eintrag (`MixEntry.stimme`).
5. Die QA misst die Stimmen, die der Renderer wirklich gelegt hat: je Eintrag
   eine RMS-Huelle der Stimme auf der Zeitleiste. Singen im Uebergang beide
   laenger als 0,5 s ueber der Schwelle, ist das ein blockierender Befund
   `stimmen`. Ungetrennte Songs ergeben den Hinweis `stimmen_ungetrennt`.

## Schritte (Test zuerst)

1. `djset/dsp/trennen.py`: `Trenner`-Protokoll, `DemucsTrenner` (lazy import),
   `trenner_fehlt()`. Optionales Extra `gesang` in `pyproject.toml`.
2. Bestand: `Stimmen` im `SongEintrag`, `Bestand.trennen`, `stimmen_pfade`.
3. Vertraege: `Stimmwechsel`, `Transition.stimmwechsel`, `MixEntry.stimme`,
   `Profile.gesang`, QaKinds `stimmen` (blockierend), `stimmen_ungetrennt`.
4. Designer: Stimmwechsel je Uebergang, `stimme` je Eintrag aus dem Profil.
5. Renderer: Stimme und Begleitung getrennt strecken, Stimmhuelle anlegen,
   Huellen zurueckgeben.
6. QA + Bericht: Ueberlagerung messen, Abschnitt "Stimmen".
7. CLI: Trennen nachholen, beim Aufnehmen trennen, `--ohne-gesang`.
8. Lauf 5 auf dem echten Bestand, Felix hoert.
